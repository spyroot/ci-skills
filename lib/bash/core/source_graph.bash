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

# Summary: List shell files tracked by the selected Git view.
# Arguments: $1: staged or all.
# Stdout: NUL-separated repository-relative paths.
# Stderr: Git diagnostics.
# Returns: Git status.
ci_source_graph_files() {
  local file first
  while IFS= read -r -d '' file; do
    case "$file" in
    *.bash | *.sh) printf '%s\0' "$file" ;;
    *)
      first="$(ci_source_graph_content "$1" "$file" | head -1)" || return
      [[ "$first" == '#!'*bash* ]] && printf '%s\0' "$file"
      ;;
    esac
  done < <(git ls-files -z)
}

# Summary: Print resolved source edges and reject gaps in selected shell files.
# Arguments: $1: staged or all.
# Stdout: tab-separated source and target paths.
# Stderr: missing annotation or target diagnostics.
# Returns: 0 when selected edges resolve; 1 on a missing dependency.
ci_source_graph_edges() {
  local scope="$1" file content target annotations sources strict=0 statement actual index
  local -A changed=() deleted=()
  local -a source_lines=() annotation_lines=()
  if [[ "$scope" == staged ]]; then
    while IFS= read -r -d '' file; do changed["$file"]=1; done \
      < <(git diff --cached --name-only -z --diff-filter=ACMR)
    while IFS= read -r -d '' file; do deleted["$file"]=1; done \
      < <(git diff --cached --name-only -z --diff-filter=D)
  fi
  while IFS= read -r -d '' file; do
    [[ "$scope" != all && -z "${changed[$file]:-}" ]] || strict=1
    content="$(ci_source_graph_content "$scope" "$file")" || return 1
    annotations="$(sed -n 's/^[[:space:]]*#[[:space:]]*shellcheck source=\([^[:space:]]*\).*/\1/p' <<<"$content")"
    sources="$(awk '/^[[:space:]]*(source|\.)[[:space:]]/{print}' <<<"$content" |
      sed '/^[[:space:]]*\. as [\$]/d')"
    if ((strict)); then
      if [[ "$(printf '%s\n' "$sources" | sed '/^$/d' | wc -l)" != "$(printf '%s\n' "$annotations" | sed '/^$/d' | wc -l)" ]]; then
        printf 'Missing or extra source annotation in %s\n' "$file" >&2
        return 1
      fi
      if [[ -n "$sources" ]]; then
        mapfile -t source_lines <<<"$sources"
        mapfile -t annotation_lines <<<"$annotations"
        for index in "${!source_lines[@]}"; do
          statement="${source_lines[$index]}"
          if [[ "$statement" =~ ^[[:space:]]*(source|\.)[[:space:]]+(.+)$ ]]; then
            actual="${BASH_REMATCH[2]}"
            actual="${actual#\"}"
            actual="${actual%\"}"
            actual="${actual#\'}"
            actual="${actual%\'}"
            if [[ "$actual" =~ ^[A-Za-z0-9._/-]+$ &&
              "$actual" != "${annotation_lines[$index]}" ]]; then
              printf 'Source annotation disagrees with literal target in %s\n' "$file" >&2
              return 1
            fi
          fi
        done
      fi
    fi
    while IFS= read -r target; do
      [[ -n "$target" ]] || continue
      if [[ "$scope" == staged ]]; then
        if ! git cat-file -e ":$target" 2>/dev/null; then
          if ((strict)) || [[ -n "${deleted[$target]:-}" ]]; then
            printf 'Missing staged Bash dependency %s from %s\n' "$target" "$file" >&2
            return 1
          fi
          continue
        fi
      elif [[ ! -f "$target" ]]; then
        printf 'Missing Bash dependency %s from %s\n' "$target" "$file" >&2
        return 1
      fi
      printf '%s\t%s\n' "$file" "$target"
    done <<<"$annotations"
    strict=0
  done < <(ci_source_graph_files "$scope")
}

# Summary: Reject a selected Bash source graph with a cycle or missing edge.
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
