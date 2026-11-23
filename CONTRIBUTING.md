# Contributing

Thank you for helping improve Workbench Coding Agent. Contributions are
welcome through pull requests.

## Before you start

- For a substantial change, open an issue first to discuss the approach.
- Keep each change focused on one bug fix or feature.
- Do not include credentials, tokens, local model data, databases, or other
  machine-specific files in a contribution.

## Prepare a change

1. Fork the repository and create a descriptive branch from `main`.
2. Make the smallest change that addresses the issue.
3. Add or update tests for behavior introduced or fixed by the change.
4. Update the README or other documentation when behavior, configuration, or
   setup instructions change.
5. Run the test suite from the repository root:

   ```bash
   ./.venv/bin/python -m pytest -q
   ```

6. Review your diff before committing. Check that it contains only intended
   changes and no secrets or generated files.

## Open a pull request

- Use a clear title that summarizes the change.
- Explain the problem and the solution, and link any related issue.
- List the tests and checks you ran, including any that could not be run.
- Include screenshots or example requests and responses when they help explain
  a user-visible change.
- Keep the pull request focused; submit unrelated changes separately.

## Project-specific considerations

Changes to agent tools affect planning, runtime authorization, acceptance
criteria, and operation journaling. Update the related schemas, tests, and
documentation together. Preserve the workspace boundary: workspace paths must
remain relative and file contents must be treated as untrusted data.
