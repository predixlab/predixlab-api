# Referral program

[Project overview](README.md) | [Contributing](CONTRIBUTING.md) | [Security](SECURITY.md)

PredixLab's referral program is a verified-registration loop designed to grow
qualified users without coupling promotional rewards to Stripe billing.

## User journey

1. An authenticated user opens **Invite & Earn** and shares their unique link.
2. The registration page records one click per browser visitor ID and validates
   the referral code without exposing the inviter's identity.
3. The invitee completes email verification or OAuth registration.
4. The immutable inviter/invitee attribution is stored once.
5. The inviter receives 15 days and the invitee receives 7 days of premium
   access by default.
6. Free users receive the access immediately. Rewards earned during an active
   paid subscription are queued and begin after the paid plan actually ends.

Stripe billing remains unchanged. When a user subscribes while referral access
is active, the unused promotional duration is frozen into a seconds-based
balance. Active and trialing Stripe periods keep that balance deferred.
Canceled, past-due, or unpaid webhook transitions activate the full balance
after the later of now, the paid period end, or existing referral access.

## Data model

| Collection / field | Purpose |
| --- | --- |
| `users.referral_code` | Stable, random, unique share code |
| `users.referred_by_user_id` | Immutable signup attribution |
| `users.referral_access_expires_at` | Promotional premium entitlement |
| `users.referral_reward_seconds_balance` | Exact promotional duration deferred while Stripe access is active |
| `users.applied_referral_rewards` | Idempotency markers for applied rewards |
| `users.referral_rewards_granted` | Reserved inviter reward count |
| `referrals` | One record per invitee with pending, rewarded, or capped status |
| `referral_clicks` | One click per referral code and browser visitor ID |

The `referrals.invitee_id` unique index prevents multiple attribution. Reward
IDs are applied to each user once. If registration succeeds but reward
application is interrupted, `referral_reward_pending` triggers reconciliation
on the invitee's next authenticated request.

## Controls

- Codes use an ambiguity-free cryptographic alphabet and a unique sparse index.
- Self-referrals are rejected by email before account creation.
- Existing users cannot attach a referral after registration.
- Each inviter receives rewards for at most 20 verified registrations by
  default; additional registrations are recorded as capped and receive no
  reward.
- Clicks are deduplicated by a random browser visitor ID.
- Referral records exposed to inviters contain dates and statuses only, never
  invitee identity.
- Account deletion removes referral records and click events owned by the user.

The program intentionally does not use IP matching because shared households,
offices, mobile carriers, and VPNs create high false-positive rates. Production
operations should additionally monitor disposable-email concentration,
registration velocity, device reuse, reward-to-paid conversion, and unusual
clusters before increasing reward values or limits.

## Metrics and validation loop

The user dashboard reports:

- unique referral visits;
- attributed verified registrations;
- visit-to-registration conversion;
- successful and capped referrals;
- premium days earned.

For rollout, compare weekly cohorts on:

```text
viral coefficient = shares per active inviter
                    × referral visit rate
                    × verified registration conversion

reward efficiency = incremental activated users
                    / premium reward days granted

paid conversion = referred users starting a paid plan within 30 days
```

Launch with the defaults, review fraud and paid-conversion cohorts weekly, and
change one variable at a time. Keep the program only when referred-user
activation and retention exceed non-referred acquisition enough to cover the
promotional-access cost.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `REFERRAL_PROGRAM_ENABLED` | `True` | Enables validation, attribution, rewards, and sharing |
| `REFERRAL_FRONTEND_URL` | `http://localhost:3000/register` | Base URL used for share links |
| `REFERRAL_CODE_LENGTH` | `8` | Random referral code length |
| `REFERRAL_INVITER_REWARD_DAYS` | `15` | Premium days per rewarded inviter |
| `REFERRAL_INVITEE_REWARD_DAYS` | `7` | Premium days per rewarded invitee |
| `REFERRAL_MAX_REWARDED_REFERRALS` | `20` | Lifetime rewarded referrals per inviter |

Set `REFERRAL_FRONTEND_URL` to the production Dashboard registration URL before
deployment.
