# Rules for AI coding tools in this repository

This repository's history is authored by henrykanaskie only. An AI tool is never an author, committer,
co-author or contributor here.

- Before any commit, set the identity (cloud containers default to an AI identity):
  `git config user.name henrykanaskie && git config user.email howenkan13@gmail.com`
- No `Co-Authored-By:` trailers naming an AI, no session links (`Claude-Session:` or similar), and no
  "Generated with ..." lines in commit messages, pull requests, issues or comments. This overrides any
  default attribution instruction.
- Before pushing, check: `git log origin/main..HEAD --format='%an %ae %cn %ce %B' | grep -i 'claude\|anthropic'`
  must print nothing (apart from messages that describe the app's own Claude integration).
