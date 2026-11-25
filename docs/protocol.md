# BLE protocol reference

The monitor only reads data — it sends the `Inquire` request (`0x13`) and nothing else.

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
