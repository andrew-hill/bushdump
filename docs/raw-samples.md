# Raw protocol samples

Captured dumps backing the claims in [`camera-api.md`](camera-api.md), kept so
future work can diff against real output instead of re-deriving it.

**Anonymised.** MAC addresses are replaced with the synthetic `AABBCC112233`
used throughout `camera-api.md`, BLE peripheral UUIDs are placeholders (they're
per-host anyway, not camera identity), and user-set location tags in BLE names
are `<location>`. Structure, UUIDs, field names and value shapes are verbatim.

Captured 2026-08-22 with `tools/probe-ssid-sources.py`.

## GATT tables

Note what is *absent*: no characteristic changes across a WiFi wake, which is
why GATT can't be polled for AP readiness. And note the System ID difference
between the two BLE modules — one carries the WiFi MAC, the other is zeroed.

### GardePro E6PMB

```
  Service 0000180a-0000-1000-8000-00805f9b34fb (Device Information)
    00002a29-0000-1000-8000-00805f9b34fb (Manufacturer Name String): 'Shenzhen RF-star Technology Co., Ltd.' hex=5368656e7a68656e2052462d7374617220546563686e6f6c6f677920436f2e2c204c74642e
    00002a27-0000-1000-8000-00805f9b34fb (Hardware Revision String): 'RF_BM_BG22A1A2' hex=52465f424d5f4247323241314132
    00002a26-0000-1000-8000-00805f9b34fb (Firmware Revision String): 'V0.3.2_2022.09.15' hex=56302e332e325f323032322e30392e3135
    00002a23-0000-1000-8000-00805f9b34fb (System ID): '<8 raw bytes>' hex=aabbccfffe112233

  Service 6e400001-b5a3-f393-e0a9-e50e24dcca9e (Nordic UART Service)
    6e400002-b5a3-f393-e0a9-e50e24dcca9e [write-without-response,write] (Nordic UART RX) — not readable
    6e400003-b5a3-f393-e0a9-e50e24dcca9e [notify] (Nordic UART TX) — not readable
    6e400004-b5a3-f393-e0a9-e50e24dcca9e [write-without-response,notify,write] (Unknown) — not readable

  Service 1d14d6ee-fd63-4fa1-bfa4-8f47b42119f0 (Unknown)
    f7bf3564-fb6d-4e53-88a4-5e37e0326063 [write] (Unknown) — not readable
    984227f3-34fc-4045-a5d0-2c581f81a153 [write-without-response,write] (Unknown) — not readable
```
### GardePro E8 2.0 Pro

```
  Service 0000180a-0000-1000-8000-00805f9b34fb (Device Information)
    00002a23-0000-1000-8000-00805f9b34fb (System ID): '\x00\x00\x00��\x00\x00\x00' hex=000000fffe000000
    00002a29-0000-1000-8000-00805f9b34fb (Manufacturer Name String): 'Shenzhen Linkiing Technology Co.,Ltd.\x00' hex=5368656e7a68656e204c696e6b69696e6720546563686e6f6c6f677920436f2e2c4c74642e00
    00002a26-0000-1000-8000-00805f9b34fb (Firmware Revision String): 'v1.0.5\x00' hex=76312e302e3500
    00002a27-0000-1000-8000-00805f9b34fb (Hardware Revision String): 'LK8625_V1.6\x00' hex=4c4b383632355f56312e3600
    00002a50-0000-1000-8000-00805f9b34fb (PnP ID): '\x02�$f�\x01\x00' hex=028a2466820100

  Service 6e400001-b5a3-f393-e0a9-e50e24dcca9e (Nordic UART Service)
    6e400003-b5a3-f393-e0a9-e50e24dcca9e [notify] (Nordic UART TX) — not readable
    6e400002-b5a3-f393-e0a9-e50e24dcca9e [write-without-response,write] (Nordic UART RX) — not readable
    6e400004-b5a3-f393-e0a9-e50e24dcca9e [write-without-response,notify,write] (Unknown) — not readable

  Service 00010203-0405-0607-0809-0a0b0c0d1912 (Unknown)
    00010203-0405-0607-0809-0a0b0c0d2b12 (Unknown): '\x00' hex=00
```
