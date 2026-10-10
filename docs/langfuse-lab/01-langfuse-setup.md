# Part 1: Run Langfuse on your laptop

In this part you start Langfuse on your own machine, next to the Northwind HR assistant, and
create the API keys the assistant will use to send Langfuse its traces. You do not change the
assistant yet. That is Part 2.

**Time:** about 20 minutes, most of it waiting for downloads.

## What Langfuse is

When somebody asks the assistant a question, a lot happens that you never see: the question is
embedded, the database is searched, weak matches are thrown away, Claude is called with a
prompt, and citations are matched up. When an answer is wrong, you need to know which of
those steps went wrong.

Langfuse records each request as a **trace**: a timeline of every step, with its inputs,
outputs, timing and cost. You open it in a web page and read exactly what happened. Later in
the lab you will also use it to score answers, run the test questions as experiments, and
raise alerts.

## What you are about to run

You will have two separate sets of containers. They do not share anything.

```
northwind-hr   (the assistant, from the repository root)     langfuse-lab   (from the langfuse folder)
  app        localhost:8501   the chat page                    langfuse-web     localhost:3000   the Langfuse page
  postgres   localhost:5432   policy chunks and chats          langfuse-worker  processes incoming traces
                                                               postgres         Langfuse's own database
                                                               clickhouse       stores traces for fast queries
                                                               redis            queue between web and worker
                                                               minio            stores raw events (like S3)
```

Langfuse needs six containers because it is built to handle millions of traces. On a laptop
you can ignore all of them except `langfuse-web`, which serves the page you open.

The assistant's containers reach Langfuse at `http://host.docker.internal:3000`. Inside a
container, `localhost` means the container itself, so Docker Desktop provides
`host.docker.internal` as the name for your computer.

## Before you start

| Check | How |
|---|---|
| Docker Desktop is running | The whale icon in the taskbar is steady, not animating |
| Docker Compose 2.24.4 or later | `docker compose version` |
| Docker has at least 6 GB of memory | Docker Desktop → Settings → Resources. Langfuse alone uses about 3 GB. |
| The Northwind assistant works | `docker compose ps` in the repository root shows `app` and `postgres` running |

Every command below is for **Windows PowerShell**, run from the repository folder, for example
`C:\Users\you\llmops-rag-prod`.

## Step 1: Create the secrets (once)

Langfuse needs passwords for its database, cache and storage, plus keys for encrypting what
it stores. A script makes a random value for each one:

```powershell
cd langfuse
.\New-LangfuseEnv.ps1
```

You should see `Created langfuse\.env with new random secrets.`

If PowerShell refuses with *"running scripts is disabled on this system"*, run it this way
instead. It allows the script for this one command only:

```powershell
powershell -ExecutionPolicy Bypass -File .\New-LangfuseEnv.ps1
```

`langfuse\.env` holds real secrets. Git ignores it, so it never ends up in a commit. Do not
send it to anyone, and do not run the script a second time: Langfuse saves these secrets in
its data the first time it starts, and new ones would lock it out. The script refuses to
overwrite the file for that reason.

## Step 2: Start Langfuse

Still in the `langfuse` folder:

```powershell
docker compose up -d
```

The first time, this downloads about 2 GB of images. When it finishes, Langfuse is still
setting up its database. Wait until this prints `OK`:

```powershell
Invoke-RestMethod http://localhost:3000/api/public/health
```

```
status version
------ -------
OK     4.56.0
```

If it says *"Unable to connect to the remote server"*, wait 30 seconds and try again. The
first start takes one to three minutes.

## Step 3: Create your account, organization and project

1. Open **http://localhost:3000** in your browser.
2. Click **Sign up**. Enter a name, an email and a password.
   This account exists only on your laptop. Nothing is sent to langfuse.com, and the email is
   never checked, so any address works.
3. Langfuse asks you to create an **organization**. Name it `Northwind`.
   An organization groups people and projects. You are its owner.
4. Skip inviting members.
5. Create a **project** named `hr-assistant`.
   A project holds the traces of one application. Its API keys only give access to that
   project.

## Step 4: Create the API keys

1. In the project, open **Settings** (the gear icon in the left sidebar), then **API Keys**.
2. Click **Create new API keys**.
3. Langfuse shows three values. **Copy them now**: the secret key is shown only once.

| Langfuse shows | Starts with | What it is |
|---|---|---|
| Secret Key | `sk-lf-` | The password. Keep it private. |
| Public Key | `pk-lf-` | The user name. Safe to show. |
| Host | `http://localhost:3000` | Where Langfuse runs, as seen from your browser |

If you lose the secret key, create a new pair and delete the old one. You cannot view a
secret key again.

## Step 5: Give the keys to the assistant

Open the `.env` file in the **repository root**. It is the one holding `ANTHROPIC_API_KEY`,
not `langfuse\.env`. Add these three lines, using your own keys:

```
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=http://host.docker.internal:3000
```

Use `host.docker.internal`, not `localhost` as Langfuse showed you. The assistant runs inside a
container, and from there `localhost` is the container itself.

## Step 6: Check the keys work

From the repository root, this reads the keys from `.env` and asks Langfuse which project they
belong to:

```powershell
$settings = @{}
Get-Content .env | Where-Object { $_ -match '^\s*([A-Z_]+)=(.*)$' } |
    ForEach-Object { $settings[$Matches[1]] = $Matches[2].Trim() }
$pair = "$($settings.LANGFUSE_PUBLIC_KEY):$($settings.LANGFUSE_SECRET_KEY)"
$token = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))
(Invoke-RestMethod http://localhost:3000/api/public/projects `
    -Headers @{ Authorization = "Basic $token" }).data | Select-Object name, id
```

It should print your project name, `hr-assistant`. A `401 Unauthorized` means a key was
copied wrongly: look for a missing character or a stray space.

This check runs on your computer, so it uses `localhost`. The assistant will use
`host.docker.internal` from inside its container.

The assistant does not send anything to Langfuse yet. Adding that is Part 2.

## Everyday commands

Run these from the `langfuse` folder.

| To | Run |
|---|---|
| Start Langfuse | `docker compose up -d` |
| See if it is running | `docker compose ps` |
| Check it is ready | `Invoke-RestMethod http://localhost:3000/api/public/health` |
| Stop it, keeping your traces and keys | `docker compose stop` |
| Start it again after a stop | `docker compose start` |
| Remove the containers, keeping your data | `docker compose down` |
| Read the web server's log | `docker compose logs langfuse-web --tail 50` |
| Follow the worker's log live (Ctrl+C to quit) | `docker compose logs -f langfuse-worker` |
| See memory use | `docker stats --no-stream` |

Stopping Langfuse never affects the assistant, and stopping the assistant never affects
Langfuse.

### Start completely from scratch

This **deletes your account, projects, keys and every trace**:

```powershell
docker compose down -v
Remove-Item .env
.\New-LangfuseEnv.ps1
docker compose up -d
```

Then repeat Steps 3 to 6, and put the new keys in the root `.env`.

## Ports

Everything listens on `127.0.0.1` only, so other computers on your network cannot reach it.

| Port | Service | You use it? |
|---|---|---|
| 3000 | Langfuse web page and API | Yes |
| 3030 | Langfuse worker | No |
| 8123, 9000 | ClickHouse | No |
| 6379 | Redis | No |
| 9090, 9091 | MinIO storage and its console | No |
| none | Langfuse's Postgres | No. The assistant's database already uses 5432, so Langfuse's has no port on your computer. |

## Troubleshooting

**`Bind for 127.0.0.1:3000 failed: port is already allocated`** (or another port number)

Another program is using that port. Find it:

```powershell
Get-NetTCPConnection -State Listen -LocalPort 3000 |
    Select-Object LocalAddress, LocalPort, OwningProcess,
        @{ n = 'Program'; e = { (Get-Process -Id $_.OwningProcess).ProcessName } }
```

Stop that program, or if it is another Docker container, stop it with `docker stop <name>`.

**The health check never says `OK`**

```powershell
docker compose ps
docker compose logs langfuse-web --tail 50
```

- A container keeps restarting: Docker Desktop is probably short of memory. Give it more
  under Settings → Resources, then run `docker compose up -d` again.
- The log mentions a password, authentication or `ENCRYPTION_KEY`: `langfuse\.env` no longer
  matches the stored data, usually because the script was run with `-Force`. Start from
  scratch as described above.

**`Unable to connect to the remote server` right after starting**

Normal for the first one to three minutes. `docker compose logs langfuse-web --tail 5`
shows `Ready` when it is done.

**`yaml: unknown tag !override` or `!reset`**

Your Docker Compose is older than 2.24.4. Update Docker Desktop.

**The login page reloads instead of signing you in**

Use exactly `http://localhost:3000`, not `127.0.0.1:3000`. Langfuse builds its login
redirects from that address.

**Costs show `$0` on every model call (Part 2)**

Langfuse prices a model call from a price table that the worker loads when it starts. If the
worker started before the database was ready, the table is missing. `docker-compose.override.yml`
makes the worker wait for the web container to prevent this, but to repair an installation where
it already happened:

```powershell
docker compose restart langfuse-worker
```

Calls made from then on are priced. Langfuse does not go back and price older ones.

**`401 Unauthorized` in Step 6**

The keys in the root `.env` do not match a key pair in this Langfuse. Check for a missing
character, a stray space or quotes around the value, or that you did not swap the public and
secret keys. If you started from scratch, the old keys are gone: create new ones.

## Upgrading Langfuse

The lab is pinned to Langfuse **4.56.0**, so everyone runs the same version. To try a newer
one, change `LANGFUSE_VERSION` in `langfuse\.env`, then:

```powershell
docker compose up -d --pull always
```

Langfuse migrates its data forward on start. There is no way back to the older version
afterwards without starting from scratch.
