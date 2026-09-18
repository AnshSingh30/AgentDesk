## What

## Why

## Checklist
- [ ] Conventional commit title (feat/fix/chore/docs/refactor/test)
- [ ] Tests added or updated; `make test` passes locally
- [ ] Tenant scoping preserved (every new query goes through `app/db/repo.py` or filters by `org_id`)
- [ ] No secrets in code, logs or fixtures
- [ ] Migration included if the model changed (`make migration m="..."`)
- [ ] Docs / runbooks updated if behaviour changed
