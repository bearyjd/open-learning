# Private deployment and backups

Use a single-user installation on a trusted private network, a VPN such as
Tailscale, or USB forwarding. Compose binds the API to loopback by default and
does not publish PostgreSQL. There is no multi-user authorization model. A bearer
token grants access to the entire library. Generate a random token of at least
32 characters, keep it out of shell history and Git, and rotate it by changing
the environment and recreating both API and worker containers. Update Android
after rotation. The app's credential is protected with the Android Keystore;
local cached reading content is still sensitive.

For access outside the host, use HTTPS and a private firewall rule. Android's
HTTP support requires explicit opt-in in connection settings; a private VPN protects traffic
in transit but does not replace server authentication. Never publish the port
to the unrestricted internet. Do not disable TLS certificate verification.

The article fetcher only permits public HTTP(S) destinations on standard ports.
It checks resolved addresses and redirects, pins the validated destination to
avoid DNS rebinding, applies deadlines and byte limits, and avoids environment
proxies. Private LAN URLs, localhost, and cloud metadata endpoints are rejected.
This is intentionally separate from the administrator-configured local AI
endpoint, which can be private. Keep the backend patched and apply egress
restrictions as an additional deployment defense.

Only extracted plain text is rendered; scripts and HTML are not displayed in a
WebView. The first milestone has no document upload endpoint. Future upload
adapters must enforce compressed and decompressed limits and prevent archive
path traversal. Source text can contain malicious model instructions; the AI
prompt treats it as data and output must cite an exact available passage. A
matching quote proves provenance, not factual correctness or logical entailment.
Tutor feedback remains labeled AI interpretation and can be wrong.

Normal API access logs are disabled in deployment because URLs and query strings
can expose reading interests. Do not enable model prompt logging on shared
machines. Container/database administrators can read the entire library. Use
encrypted storage and encrypted backup destinations where appropriate. Do not
commit exports, database dumps, `.env`, credentials, or reading history.

## Backup and restore

From the repository root, with the same `.env` as the running installation:

```sh
mkdir -p private-backups
chmod 700 private-backups
podman compose --env-file .env -f infrastructure/compose.yaml exec -T db \
  pg_dump -U openlearning -d openlearning -Fc > private-backups/knowledge.dump
```

Store `private-backups` outside the checkout or add it to your private ignore
rules. Back up `.env` separately in an encrypted password manager. To restore,
stop API and worker first and restore into a fresh database using the same
PostgreSQL major version:

```sh
podman compose --env-file .env -f infrastructure/compose.yaml stop api worker
podman compose --env-file .env -f infrastructure/compose.yaml exec -T db \
  pg_restore -U openlearning -d openlearning --clean --if-exists < private-backups/knowledge.dump
podman compose --env-file .env -f infrastructure/compose.yaml run --rm migrate
podman compose --env-file .env -f infrastructure/compose.yaml up -d api worker
```

Test restores periodically on an isolated instance. A dump contains private
reading content even if the original source was public. Uninstalling Android
removes its local cache; the canonical library remains in PostgreSQL.
