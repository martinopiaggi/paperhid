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

## Post-import modifications

These files are **intentionally modified** relative to the pinned archives (not
verbatim copies). Treat the pin as provenance for the bulk of device/shared
logic; monorepo integration lives in the deltas below.

| Path | Pin origin | Why changed in PaperHid |
|------|------------|-------------------------|
| `keyboard_cli.py` | PaperWriter `cli.py` | Password resolution via `core.credentials` (CLI-first precedence) |
| `paperpointer/cli.py` | PaperPointer package | Preflight before pointer upload; `register_pointer_commands` / `dispatch_pointer` API; shared-flag `SUPPRESS` for before/after subcommand flags |
| `paperpointer/sshutil.py` | PaperPointer package | Password via monorepo precedence (`require_password`) |
| `paperpointer/__main__.py` | PaperPointer package | Guard `main()` with `if __name__ == "__main__"` |
| `paperpointer/__init__.py` | PaperPointer package | Version bump for monorepo package |
| `shared/bluetooth.py` | PaperWriter | `service_present` / `service_active` / `service_failed` for status health |
| `core/*` new modules | — | credentials, connection policy, status_merge |
| Root `cli.py`, `README.md`, tests | — | Unified install/status, merge tests |

## Relationship to the three repositories

| Repository | Visibility (at PaperHid creation) | Role |
|------------|-----------------------------------|------|
| `paperwriter` | public | Keyboard-focused product |
| `paperpointer` | public | Pointer-focused product |
| `paperhid` | **private** | Union + unified install/status CLI |

Deprecation of the split repos is **not** part of this merge; only after device smoke.
