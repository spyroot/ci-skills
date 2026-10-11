# GitLab MCP configuration

The `mcpServers` entry in your MCP client's configuration selects the GitLab endpoint. Replace the example host with
that instance's endpoint:

```json
{
  "mcpServers": {
    "GitLab": {
      "type": "http",
      "url": "https://gitlab.example.com/api/v4/mcp",
      "headers": {
        "X-Gitlab-Mcp-Server-Tool-Name-Prefix": "gitlab_",
        "X-Gitlab-Enabled-Mcp-Server-Toolsets": "core,wikis"
      }
    }
  }
}
```

`X-Gitlab-Enabled-Mcp-Server-Toolsets`, the request header in this configuration, selects comma-separated tool groups.
The example opts into `wikis`. The `meta` group is always available. Other groups include `merge_requests`, `work_items`,
`repository`, `ci`, `duo_agent_platform`, and `code_security`; availability depends on the selected instance.

`X-Gitlab-Mcp-Server-Tool-Name-Prefix`, the other header above, prefixes tool names to distinguish GitLab instances.
Prefixes longer than 32 characters are truncated. Authenticate through the selected client's supported GitLab flow;
this reference does not contain credentials or create an MCP server.
