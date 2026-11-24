# Releasing

A release is an annotated tag `vX.Y.Z` on `main` whose message is the release
notes. There is no changelog file: `git show vX.Y.Z` prints a release's notes,
and its GitHub release shows the same text.

1. Collect the pull requests merged since the last release:

   ```bash
   gh pr list --state merged --base main --search "merged:>YYYY-MM-DD" --json number,title
   ```

2. Write `notes.md`. The first line is one sentence that says what the release
   is about. Then, after a blank line, the changes grouped by area, each naming
   its pull request. Put anything users must act on first.

   ```markdown
   Figures are written to the store named by the inserting connection's config.

   ## Storage

   - `<figpack@>` resolves its store through the calling connection (#123)
   ```

3. Tag the release commit on `main` and push the tag:

   ```bash
   git tag -a vX.Y.Z --cleanup=verbatim -F notes.md && git push origin vX.Y.Z
   ```

   `--cleanup=verbatim` keeps lines that start with `#`, such as Markdown
   headings, which git would otherwise drop as comments.

Pushing the tag runs the [CD workflow](workflows/cd.yml). It checks that the tag
is annotated and on `main`, and refuses a lightweight tag (one made without
`-a`); runs the CI workflow; publishes the package built at the tag through PyPI
trusted publishing; and creates the GitHub release, with the tag's message as
its notes and the wheel and sdist attached.

The version comes from the tag: hatch-vcs derives it at build time, so there is
no version number to edit, and the workflow checks that the built files carry
the tag's version. Only `vX.Y.Z` tags count; the earlier `0.2.0` and `0.2.1`
tags, made without the `v` and never published to PyPI, are ignored. If a job
fails after the tag is pushed, fix the cause and re-run the failed jobs from the
workflow run; a fix that needs a new commit is released under the next version.
Never move or delete a pushed tag: the version on PyPI and the GitHub release
are tied to it.
