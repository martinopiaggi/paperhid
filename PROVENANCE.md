# Provenance

PaperHid is the monorepo union of two source repositories, imported from
**exact pinned commits** (clean `git archive` trees — not dirty working copies).

| Component | Source repository | Pinned commit | Role |
|-----------|-------------------|---------------|------|
| Keyboard / BT service / GUI | [paperwriter](https://github.com/martinopiaggi/paperwriter) | `3f752ca` | Pairing, NXP radio, layout patch, desktop app |
| Pointer / mouse / cursor | [paperpointer](https://github.com/martinopiaggi/paperpointer) | `4174a57` | `paperpointerd`, uinput multitouch, QML cursor |

## Intentional exclusions

- Untracked / local-only files from source worktrees were **not** imported
  (e.g. PaperWriter’s untracked `docs/MOUSE_PAPER_PRO_PLAN.md` if present only locally).
- Monorepo glue (root `cli.py`, `core/credentials.py`, `core/connection.py`,
  `core/status_merge.py`, this file, root README) is **new** in PaperHid.

## Relationship to the three repositories

| Repository | Visibility (at PaperHid creation) | Role |
|------------|-----------------------------------|------|
| `paperwriter` | public | Keyboard-focused product |
| `paperpointer` | public | Pointer-focused product |
| `paperhid` | **private** | Union + unified install/status CLI |

Deprecation of the split repos is **not** part of this merge; only after device smoke.
