# 2026-09-29: find the CTS server's internet address

**Why.** The digibuild API only accepts the alarm bot's uploads from the building's own internet address (an
allow-list on the VPS, on top of the signature). That address has to be found on the CTS server itself. The same
check shows whether the server can reach `api.digibuild.dk` at all, and whether its clock is close enough: the
upload's signature is refused when the clock is more than 300 seconds off.

**What it changes.** Nothing. It asks two public services (`api.ipify.org`, `icanhazip.com`) which address they see,
calls `https://api.digibuild.dk/api/cts-alarms/healthz` once, and compares the clock with that answer's `Date`.

## Run it

1. In `C:\cts-api`, pull the repo (`git pull`, or `update.cmd` of any application).
2. Double-click **`C:\cts-api\runbooks\2026-09-29-internet-address.cmd`**. Answer **Yes** to the administrator
   question.
3. Read the green line **`THIS SERVER'S INTERNET ADDRESS: …`** and tell that address to the person setting up
   digibuild. **Do not commit it**: this repository is public.

## Success

```
OK internet address found, api.digibuild.dk reachable, clock fine.
```

`HTTP 404 (reachable; the CTS part is not deployed yet)` is a success too: it proves the network path works.

## If it fails

| Message | Meaning | Next step |
|---|---|---|
| `no service could tell the internet address` | outbound HTTPS is blocked | ask the building's IT to allow HTTPS to `api.digibuild.dk` |
| `api.digibuild.dk cannot be reached` | the same, for digibuild | as above |
| `this server's clock is N s off` | the upload would be refused | fix the time (`w32tm /resync` in Command Prompt (Admin)) and run this again |

## Undo

Nothing to undo.
