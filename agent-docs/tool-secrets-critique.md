Overall, this is a reasonable design for a first implementation. It cleanly separates secret storage from tool configuration and avoids painting you into a corner. There are, however, several areas where I would change the design before implementing it.

## What I Like

### 1. Separate credential store

This is the strongest part of the proposal.

Storing credentials independently from tools gives you:

* Reuse (`jira-prod` can be used by multiple tools)
* Easier rotation
* Cleaner domain boundaries
* Future support for assigning credentials to tools, users, workflows, etc.

I would keep this.

### 2. Spec-from-implementation

Having tools declare:

```python
config_schema
secret_schema
```

is a good approach.

The frontend can discover requirements dynamically without hardcoding tool types.

This is much more maintainable than maintaining parallel backend and frontend definitions.

### 3. Credential service abstraction

Good.

Most application code should never know:

* encryption algorithm
* storage location
* database schema

A service layer is the right place.

### 4. Name-based lookup

```text
brave.api_key
jira.production
github.personal
```

is simple and flexible.

I would probably reserve certain namespaces early:

```text
system.*
user.*
tool.*
```

even if you don't use them immediately.

---

## Things I Would Change

### 1. Don't implement plaintext fallback

This is my biggest objection.

Current behavior:

```text
MOIRA_SECRETS_KEY missing
→ silently store plaintext
```

even with a warning.

That means:

* development accidentally becomes production
* backups contain plaintext secrets
* people forget to set the key

I would instead make it explicit.

Options:

#### Option A

Refuse startup.

```text
CredentialService unavailable:
MOIRA_SECRETS_KEY not configured.
```

#### Option B (my preference)

Allow plaintext only in explicit development mode:

```bash
MOIRA_ALLOW_PLAINTEXT_SECRETS=true
```

Otherwise fail.

That prevents accidental exposure.

---

### 2. PBKDF2 is solving the wrong problem

This section raised a flag:

```python
PBKDF2HMAC(
    master_key,
    salt,
    iterations=600_000
)
```

PBKDF2 exists primarily to derive keys from low-entropy passwords.

Your master key should not be a password.

It should be:

```text
32+ random bytes
```

stored in an environment variable.

If that's true, PBKDF2 adds little security.

You could simply do:

```python
HKDF(master_key, salt)
```

or even

```python
HMAC(master_key, salt)
```

to derive per-secret keys.

PBKDF2 at 600k iterations will add noticeable CPU cost every credential read/write.

For a credential store that's probably not catastrophic, but it's unnecessary.

---

### 3. Reusing salt is fine

The proposal says:

> generate a new salt on update

This isn't harmful, but it doesn't really buy you anything.

A salt isn't a rotation secret.

Its purpose is uniqueness.

I would simply:

```text
salt generated once
stored forever
```

unless the credential itself changes.

This makes updates simpler.

---

### 4. Consider storing encryption metadata

Today:

```sql
encrypted_data
salt
```

Tomorrow you may want:

* Fernet → AES-GCM
* PBKDF2 → HKDF
* iteration count changes

I'd strongly consider:

```sql
encryption_version INTEGER NOT NULL
```

from day one.

Even if:

```text
version = 1
```

forever initially.

Future migrations become much easier.

---

### 5. The unlock endpoint makes me nervous

This endpoint:

```http
POST /api/credentials/{name}/unlock
```

returns plaintext secrets.

That's reasonable for a credential management UI.

However:

* browser dev tools can see it
* frontend memory can see it
* logs can accidentally see it

I would define the use case carefully.

Questions:

* Is this only for viewing credentials?
* Will tools call it?
* Does the frontend need plaintext at all?

Many systems never allow re-viewing a secret after creation.

Example:

```text
**************
```

with only:

```text
Replace credential
```

available.

Not saying you must do that, but I'd think carefully before exposing a general-purpose "give me the secret" API.

---

### 6. Credential types may be over-specified

Today:

```python
api_key
username_password
bearer_token
```

The difference is mostly UI.

All three are:

```json
{
  "field": "value"
}
```

I might instead store:

```json
{
  "api_key": "..."
}
```

or

```json
{
  "username": "...",
  "password": "..."
}
```

and let schemas define shape.

Then the database becomes:

```sql
name
secret_json
```

without a fixed credential type enum.

This becomes important when you eventually need:

```text
oauth2
aws credentials
client certificate
ssh key
api key + organization id
```

Otherwise you'll keep extending the enum forever.

---

## One Architectural Question

You're introducing both:

```python
CredentialService
```

and

```python
secret_schema
```

but there is no mapping between them.

Eventually you'll need something like:

```python
class BraveSearchTool(BaseTool):
    secret_schema = {
        "api_key": ...
    }

    credential_binding = {
        "api_key": "brave.api_key"
    }
```

or

```python
tool_instance.secret_refs = {
    "api_key": "brave.api_key"
}
```

I would not implement this now, but I would think about it before finalizing the credential model because it influences how tools discover credentials.

---

## One Small SQL Issue

This repository method description:

> INSERT OR REPLACE

In SQLite, `REPLACE` is actually:

```sql
DELETE
INSERT
```

under the hood.

That means:

* `created_at` may be lost
* foreign keys can break
* triggers fire differently

Prefer:

```sql
INSERT INTO ...
ON CONFLICT(name)
DO UPDATE SET ...
```

instead.

---

## My Suggested Changes Before Implementation

1. Keep the separate credential store.
2. Keep the spec-from-implementation design.
3. Remove automatic plaintext fallback; require an explicit development-mode override.
4. Replace PBKDF2 with HKDF if `MOIRA_SECRETS_KEY` is already a random secret.
5. Add `encryption_version` now.
6. Use `ON CONFLICT DO UPDATE` instead of `INSERT OR REPLACE`.
7. Reconsider whether `credential_type` needs to be fixed in the database schema.
8. Carefully define who is allowed to call `/unlock` and why.

If this were my project, the two changes I'd consider most important are **eliminating implicit plaintext storage** and **adding an encryption version field before the schema ships**. Those are the ones most likely to cause regret later.
