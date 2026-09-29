# Getting cts-api onto the CTS server

The repo lives in **`C:\cts-api`** on the CTS server. Each application then has its own install in its folder,
for example `C:\cts-api\cts-alarms\install.cmd`. Everything below is done on the CTS server itself: open a
browser there, or type the short commands. Nothing needs to be pasted from another machine.

The repo is public, so no GitHub login is needed.

## Once: put the repo in C:\cts-api

### With Git (recommended: later updates are one `git pull`)

1. If Git is not installed: in the server's browser, type **`https://git-scm.com/download/win`**, download
   the 64-bit installer, and install it with the default choices.
2. Open **Command Prompt (Admin)**: right-click the Start button, then **Command Prompt (Admin)**. Type:

   ```
   git clone https://github.com/slimyloki/cts-api.git C:\cts-api
   ```

### Without Git (ZIP)

1. In the server's browser, type **`https://github.com/slimyloki/cts-api/archive/refs/heads/main.zip`**.
2. Right-click the downloaded ZIP, choose **Properties**, tick **Unblock** if it is there, and click **OK**.
3. Right-click the ZIP, choose **Extract All…**, and extract to **`C:\`**. This gives `C:\cts-api-main`.
4. Rename **`C:\cts-api-main`** to **`C:\cts-api`**.

## Then: install an application

Open the application's `INSTALL.md` in `C:\cts-api` (or on GitHub) and follow it. Today that is:

| Application | Install |
|---|---|
| cts-alarms (alarm bot) | Double-click **`C:\cts-api\cts-alarms\install.cmd`**; see [cts-alarms/INSTALL.md](cts-alarms/INSTALL.md) |

## Updating

Each application's **`update.cmd`** brings `C:\cts-api` up to date, then reinstalls that application:
- **With Git**, it runs `git pull`.
- **Without Git**, it downloads the ZIP and copies it over `C:\cts-api`.

To update the repo by hand, type this in Command Prompt (Admin):

```
cd /d C:\cts-api
git pull
```

**Applications run in place from `C:\cts-api`**, so a pull changes the code their next run uses. Prefer the
application's **`update.cmd`**: it pauses the application, backs up, pulls, test-runs, and puts the previous code
back if the test fails. A plain `git pull` skips those checks; after one, run the application's `install.cmd`
(or `status.cmd`) to confirm it still works.

## One-off actions

When something has to be done on the server once, for example moving a folder or changing a scheduled
task, it comes as a script in [`runbooks/`](runbooks/README.md). Pull, open the runbook's `.md`, and
double-click its `.cmd`.
