---
title: "Choosing a strong password"
description: How long a password should be, and why 15 characters is a good start.
lang: en
---

# Choosing a strong password

Passwords must be at least 15 characters long[1].

You may use a *password manager* to store them.

## What to check

- Use a different password for every site.
- Turn on two-step sign-in.
  - Prefer an app over text messages.
- Change a password after a breach.

| Length | Time to guess |
|---|---|
| 8 characters | 2 days |
| 15 characters | 400 years |

Run `pwgen -s 15` to make one, or read [the guide][guide].

> A long passphrase is easier to remember than a short, complex one.

```sh
pwgen -s 15 1
```

![Chart of guessing time](/img/chart.png)

[guide]: https://example.com/guide "Password guide"
