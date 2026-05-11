# ops/cron — MolTrust scheduled jobs

## moltrust-probe-gc

Daily 04:00 UTC: drops unclaimed probe DIDs older than 7 days (per
docs/auto-probe-token-spec.md §8). Cascading FKs clean probe_activity
and conversion_funnel automatically.

### Install (root):

```
sudo cp moltrust-probe-gc /etc/cron.d/moltrust-probe-gc
sudo chown root:root /etc/cron.d/moltrust-probe-gc
sudo chmod 644 /etc/cron.d/moltrust-probe-gc
sudo touch /var/log/moltrust-probe-gc.log
sudo chown moltstack:moltstack /var/log/moltrust-probe-gc.log
```

### Verify:

```
grep CRON /var/log/syslog | grep moltrust-probe-gc | tail
tail -f /var/log/moltrust-probe-gc.log
```
