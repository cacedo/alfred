# GitLab Project Search

Alfred workflow for searching GitLab projects and repositories with `gl`.

The packaged workflow file is [`GitLab-Project-Search.alfredworkflow`](/Users/carlos.acedo/codex/alfred/gitlab/GitLab-Project-Search.alfredworkflow) at the repository root. Python scripts live under [`scripts/`](/Users/carlos.acedo/codex/alfred/gitlab/scripts).

## Setup

Open Alfred, go to Workflows, open `GitLab Project Search`, then use `Configure Workflow` to set:

1. `GITLAB_HOST`
2. `GITLAB_USER`
3. `GITLAB_TOKEN`

## Usage

- `gl <search terms>` searches projects and repos.
- If variables are missing, the workflow shows a setup reminder in Alfred.
