# Runbook: rotate the encryption master key

Customer secrets use envelope encryption: a per-record data key wrapped by a master key from `ENCRYPTION_MASTER_KEYS` (comma-separated base64, first = active).

1. Generate a new key: `python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"`.
2. Set `ENCRYPTION_MASTER_KEYS=<new>,<old>` and restart API/worker. New writes use the new key; old blobs still decrypt.
3. Re-encrypt existing rows (provider_credentials, datasets, deployments) by re-saving them, or run a one-off script calling `decrypt_secret` + `encrypt_secret`.
4. Remove the old key from the list once no blob references index 1 (blobs record the key index).
