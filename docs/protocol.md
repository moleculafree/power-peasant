# BLE protocol reference

The monitor only reads data — it sends the `Inquire` request (`0x13`) and nothing else.

## Compatibility

The monitor works with BMS units built on this protocol. The same platform is
used by several brands:

- Power Queen (`P-…` names);
- Redodo (`R-…` names);
- LiTime (`LT-…`, `L-…` names);
- Starry Sea (`S-…`, `SS-…`) — reported to use the same platform, not tested.

Verified on real hardware: Power Queen 12.8 V 100 Ah (`P-12100BNNA70`), 12 V
140 Ah and 24/25.6 V 100 Ah; Redodo 12.8 V 280 Ah (`R-12280BNN200`); LiTime
12.8 V 100 Ah, 24 V 50 Ah, 12 V and 36 V. Other capacities from these families
are expected to work; the frame format supports up to 16 cells (36 V and 48 V
packs are untested).

The device must advertise service `FFE0` and expose `FFE1` (notifications) and
`FFE2` (writes), and must answer the `Inquire` request with a valid 105-byte
frame. A **BMS** badge in the scan list only means `FFE0` was advertised; it
does not prove the device speaks this protocol. Batteries that use JBD/Xiaoxiang
(`DD A5 …`), JK (`AA 55 90 EB`), Daly, Seplos and similar protocols may appear
in the scan but will not return valid frames.

Before an advertising battery is added manually, the monitor performs this check
on the real device (one `Inquire` request plus the firmware `0x16` request) and
refuses to persist it if no valid frame arrives. A battery added from discovery
history is checked on its first connection instead.

## Service and frame format

- Service: `0000FFE0-0000-1000-8000-00805F9B34FB`
- Write characteristic: `0000FFE2-...`, notifications: `0000FFE1-...`
- Request: `00 00 04 01 13 55 AA 17`
  (command `0x13`, checksum = sum of all preceding bytes mod 256)
- Response: 105 bytes, `[4] = 0x93`, header `55 AA` at `[5..6]`

| Bytes | Meaning |
|---|---|
| `[8..11]` | pack capacity (LE u32) |
| `[12..15]` | pack voltage, mV (LE u32) |
| `[16..47]` | up to 16 cells, 2 bytes LE each (mV); list ends at `00 00` |
| `[48..51]` | current, mA (LE s32) |
| `[52..53]` | cell temperature, °C (s16) |
| `[54..55]` | MOSFET temperature, °C (s16) |
| `[62..63]` | remaining capacity, 0.01 Ah |
| `[64..65]` | full charge capacity, 0.01 Ah |
| `[68..71]` | self-heating flags |
| `[76..79]` | protection state |
| `[80..82]` | failure state |
| `[84..87]` | cell balancing bitmask |
| `[90]` | SOC, % |
| `[92]` | SOH, % |
| `[96..99]` | cycle count |
| `[100..103]` | total discharged, Ah |
| `[104]` | checksum (sum of bytes 0..103 mod 256) |
