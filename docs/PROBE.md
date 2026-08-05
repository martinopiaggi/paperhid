# Live probe notes (Paper Pro)

Device: `imx8mm-ferrari`, kernel `6.12.49+git-imx8mm-ferrari`, OS ~3.28.

## Input nodes (stock)

| Node | Name | Role |
|------|------|------|
| event0 | snvs-powerkey | power button |
| event1 | Hall effect sensors | folio hall |
| event2 | Elan marker input | pen — ABS_X 0..11180, ABS_Y 0..15340 |
| event3 | Elan touch input | finger MT |

### Elan touch abs (injection target)

```
ABS_MT_SLOT           0..9
ABS_MT_POSITION_X     0..2064
ABS_MT_POSITION_Y     0..2832
ABS_MT_TRACKING_ID    0..65535
ABS_MT_PRESSURE       0..255
PROP                  INPUT_PROP_DIRECT
```

### uinput

`/dev/uinput` exists. `modprobe uinput` available.

### Bluetooth

- `btnxpuart` + `uhid` loaded when PaperWriter service active
- Paired: CLVX S `C2:8B:E9:E7:7B:D5` (saved) and `C2:B3:C3:30:75:FF`
- Connect while keyboard asleep → `le-connection-abort-by-local` (wake pad/keys)

### DRM

`card0-LVDS-1` reports mode `405x1084` (not used for inject; touch abs space is source of truth).

## Decision

Default path: **mouse/touchpad → uinput multitouch** matching event3 ranges (M2 in MOUSE plan).
