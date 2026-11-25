# Metrics (thingsboard, run measure-20261126-000140)

| Latency (live samples) | n | mean | median | P95 | max |
|---|---|---|---|---|---|
| rx_ms - ts (server stamped) | 1847 | 30.1 | 30.0 | 45.0 | 736.0 |
| lower bound: PUBLISH - ts | 1847 | 10.7 | 9.0 | 15.0 | 701.0 |
| upper bound: PUBACK - ts | 1847 | 17.7 | 15.0 | 25.0 | 710.0 |

Delivery ratio (store-and-forward nodes): 100.00 % present on server (2160/2160); 100.00 % confirmed synced by PUBACK; outage 180 s
Data loss after full sync: 0 rows (0.000 %), republished rows 3

| Node | depth at reconnect | rows | drain s | rows/s | recovery s (from restore) |
|---|---|---|---|---|---|
| BID-01 | 91 | 91 | 0.08 | 1151.9 | 1.37 |
| BID-02 | 99 | 99 | 0.10 | 1042.1 | 16.84 |
| BID-03 | 105 | 105 | 0.10 | 1029.4 | 29.91 |

Alarm activation delay, live threshold alarms: n=21 mean=550.3 ms median=437.0 ms max=1082.0
Buffered-breach alarms: n=2 mean=185054.5 ms
Node Inactive detection: n=6 mean=33561.0 ms
