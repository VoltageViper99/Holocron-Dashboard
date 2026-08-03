# Holocron Port Manager architecture

`main.sh` only schedules renewals and coordinates provider, application, log,
and state modules. Proton NAT-PMP details live in `providers/proton.sh`.
qBittorrent Web API details live in `applications/qbittorrent.sh`.

The service renews its mapping every 45 seconds. It updates only qBittorrent,
and reads qBittorrent's preferences back before recording a successful update.

The public interface is the atomically replaced JSON file:

```text
/var/lib/holocron/port-manager/status.json
```

The `applications` field is an array. Dashboard code iterates that array and
does not contain qBittorrent-specific presentation logic, so future adapters
can add Transmission or rTorrent entries without a dashboard redesign.

Credentials are stored only in `/etc/holocron/port-manager.conf` with mode
`0600`. They are never written to status JSON or logs.
