"""Fixed, labelled fixture generation; never derives real production measurements."""
from datetime import datetime, timedelta, timezone
from .workflow import BATCH


def sample_rows():
    start = datetime(2026, 9, 21, 4, tzinfo=timezone.utc)
    names = ['循环冷却泵', '液压站电机', '排风机', '给料电机', '备用水泵', '离线传感设备']
    assets, samples = [], []
    for i,name in enumerate(names, 1):
        identity = f'DEMO-EQ-{i:03d}'
        assets.append((identity, '演示-'+name, 80, 1800, 1, BATCH))
        for hour in range(24):
            timestamp = start + timedelta(hours=hour)
            state, temperature, rpm = 'running', 45+i*2+hour*0.25, 1300+i*30+hour*2
            boot = start - timedelta(hours=i)
            uptime = (timestamp-boot).total_seconds()
            if i == 2 and hour >= 20: temperature=82+(hour-20)*2
            if i == 3 and hour >= 22: rpm=1850+(hour-22)*30
            if i == 4 and hour >= 22:
                state, rpm, uptime = 'fault', 0, (22+i)*3600
            if i == 5:
                state, rpm, temperature, uptime = 'stopped', 0, 25.0, 0
            if i == 6 and hour >= 20:
                state, rpm, temperature, uptime, boot = 'offline', None, None, None, None
            samples.append((identity, timestamp.replace(tzinfo=None), state,
                boot.replace(tzinfo=None) if boot else None, uptime, rpm, temperature, 1, BATCH))
    return assets, samples
