#!/usr/bin/env bash
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com

# Summary: Read one Bash file from the selected Git view.
# Arguments: $1: staged or all; $2: repository-relative path.
# Stdout: file bytes.
# Stderr: Git or cat diagnostics.
# Returns: reader status.
ci_source_graph_content() {
  if [[ "$1" == staged ]]; then git show ":$2"; else cat -- "./$2"; fi
}

# Summary: List every added, copied, modified, or renamed index path.
# Arguments: none.
# Stdout: NUL-separated repository-relative paths.
# Stderr: Git diagnostics.
# Returns: 0 on success; a Git failure status otherwise.
ci_source_graph_selected_staged_paths() (
  git diff --cached --name-only -z --diff-filter=ACMR
)

# Summary: List shell files in the selected index or nonignored working view.
# Arguments: $1: staged or all.
# Stdout: NUL-separated repository-relative paths.
# Stderr: Git diagnostics.
# Returns: Git status.
ci_source_graph_files() (
  local file first inventory
  inventory="$(mktemp)" || return
  trap 'rm -f -- "$inventory"' EXIT
  if [[ "$1" == staged ]]; then
    git ls-files -z >"$inventory" || return
  else
    git ls-files --cached --others --exclude-standard -z >"$inventory" || return
  fi
  while IFS= read -r -d '' file; do
    [[ "$1" != all || -f "./$file" ]] || continue
    case "$file" in
    *.bash | *.sh | *.bats | .githooks/*) printf '%s\0' "$file" ;;
    *)
      first="$(ci_source_graph_content "$1" "$file" | sed -n '1p')" || return
      if [[ "$first" == '#!'*bash* ]]; then
        printf '%s\0' "$file"
      fi
      ;;
    esac
  done <"$inventory"
  return 0
)

# Summary: Read literal source targets that do not need an annotation.
# Arguments: $1 Bash source text.
# Stdout: one relative target per line.
# Returns: 0.
ci_source_graph_literals() {
  sed -n -E \
    -e 's/^[[:space:]]*(source|\.)[[:space:]]+"([A-Za-z0-9._/-]+)".*/\2/p' \
    -e "s/^[[:space:]]*(source|\.)[[:space:]]+'([A-Za-z0-9._/-]+)'.*/\\2/p" \
    -e 's/^[[:space:]]*(source|\.)[[:space:]]+([A-Za-z0-9._/-]+)([[:space:]]|$).*/\2/p' \
    <<<"$1"
}

# Summary: Require a declared target beside each dynamic source in changed Bash.
# Arguments: $1 Bash source text; $2 repository-relative path.
# Stderr: the path of an undeclared dynamic source.
# Returns: 0 when each dynamic source is declared; 1 otherwise.
ci_source_graph_dynamic_annotations() {
  local content=$1 file=$2 line annotation=0
  while IFS= read -r line; do
    if [[ $line =~ ^[[:space:]]*#[[:space:]]*shellcheck[[:space:]]+source=[^[:space:]]+ ]]; then
      annotation=1
      continue
    fi
    if [[ $line =~ ^[[:space:]]*(source|\.)[[:space:]]+ ]]; then
      if [[ -z $(ci_source_graph_literals "$line") && $annotation -ne 1 ]]; then
        printf 'Dynamic Bash source in %s needs a shellcheck source annotation\n' "$file" >&2
        return 1
      fi
      annotation=0
    elif [[ ! $line =~ ^[[:space:]]*(#|$) ]]; then
      annotation=0
    fi
  done <<<"$content"
}

# Summary: Print source edges and reject gaps in changed staged shell files.
# Arguments: $1 staged or all.
# Stdout: tab-separated source and target paths.
# Stderr: missing selected target diagnostics.
# Returns: 0 when edges resolve; 1 on an invalid changed source.
ci_source_graph_edges() (
  local scope="$1" file content target annotations literals targets
  local inventory_dir selected_inventory deleted_inventory files_inventory
  local -A changed=() deleted=()
  inventory_dir="$(mktemp -d)" || return
  trap 'rm -rf -- "$inventory_dir"' EXIT
  files_inventory="$inventory_dir/files"
  ci_source_graph_files "$scope" >"$files_inventory" || return
  if [[ "$scope" == staged ]]; then
    selected_inventory="$inventory_dir/selected"
    deleted_inventory="$inventory_dir/deleted"
    ci_source_graph_selected_staged_paths >"$selected_inventory" || return
    git diff --cached --no-renames --name-only -z --diff-filter=D \
      >"$deleted_inventory" || return
    while IFS= read -r -d '' file; do changed["$file"]=1; done \
      <"$selected_inventory"
    while IFS= read -r -d '' file; do deleted["$file"]=1; done \
      <"$deleted_inventory"
  fi
  while IFS= read -r -d '' file; do
    content="$(ci_source_graph_content "$scope" "$file")" || return 1
    if [[ "$scope" == staged && -n "${changed[$file]:-}" ]]; then
      ci_source_graph_dynamic_annotations "$content" "$file" || return 1
    fi
    annotations="$(sed -n 's/^[[:space:]]*#[[:space:]]*shellcheck source=\([^[:space:]]*\).*/\1/p' <<<"$content")"
    literals="$(ci_source_graph_literals "$content")" || return 1
    targets="$(printf '%s\n%s\n' "$annotations" "$literals" | sed '/^$/d' | sort -u)" ||
      return 1
    while IFS= read -r target; do
      [[ -n "$target" ]] || continue
      target="${target#./}"
      if [[ "$scope" == staged ]]; then
        if ! git cat-file -e ":$target" 2>/dev/null; then
          if [[ -n "${changed[$file]:-}" || -n "${deleted[$target]:-}" ]]; then
            printf 'Missing staged Bash dependency %s from %s\n' "$target" "$file" >&2
            return 1
          fi
          continue
        fi
      elif [[ ! -f "$target" ]]; then
        continue
      fi
      printf '%s\t%s\n' "$file" "$target"
    done <<<"$targets"
  done <"$files_inventory"
)

# Summary: Reject a selected Bash source graph with a cycle or invalid edge.
# Arguments: $1: staged or all.
# Stderr: one dependency or cycle diagnostic.
# Returns: 0 if acyclic and resolved; 1 otherwise.
ci_source_graph_acyclic() {
  local scope="$1" from to mid node changed=1 edges
  local -A reach=()
  edges="$(ci_source_graph_edges "$scope")" || return 1
  while IFS=$'\t' read -r from to; do
    [[ -n "$from" ]] || continue
    reach["$from"]+=" $to"
  done <<<"$edges"
  while ((changed)); do
    changed=0
    for node in "${!reach[@]}"; do
      for mid in ${reach["$node"]}; do
        for to in ${reach["$mid"]:-}; do
          [[ " ${reach["$node"]} " == *" $to "* ]] && continue
          reach["$node"]+=" $to"
          changed=1
        done
      done
    done
  done
  for node in "${!reach[@]}"; do
    if [[ " ${reach["$node"]} " == *" $node "* ]]; then
      printf 'Bash source cycle includes %s\n' "$node" >&2
      return 1
    fi
  done
}
