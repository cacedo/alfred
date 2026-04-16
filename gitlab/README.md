# GitLab Project Search

Alfred workflow for searching GitLab projects and repositories with `gl`.

The packaged workflow file is [`GitLab-Project-Search.alfredworkflow`](/Users/carlos.acedo/codex/alfred/gitlab/GitLab-Project-Search.alfredworkflow) at the repository root. Python scripts live under [`scripts/`](/Users/carlos.acedo/codex/alfred/gitlab/scripts).

## Setup

Open Alfred, go to Workflows, open `GitLab Project Search`, then use `Configure Workflow` to set:

1. `GITLABHOST`
2. `GITLABUSER`
3. `GITLAB_TOKEN`

The scripts also continue to accept the legacy `GITLAB_HOST` and `GITLAB_USER` variable names for compatibility.

## Usage

- `gl <search terms>` searches projects and repos.
- If variables are missing, the workflow shows a setup reminder in Alfred.
