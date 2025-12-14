# PRD: Kryten Moderator Service

Version: 1.0.0

## 1. Product overview

### 1.1 Document title and version

- PRD: Kryten Moderator Service
- Version: 1.0.0

### 1.2 Product summary

The Kryten Moderator Service is a microservice that provides automated and manual chat moderation capabilities for CyTube/sync channels through the Kryten bridge. It addresses critical limitations in the native CyTube moderation system where users can only be actioned while online, and where IP-based ban evasion is not natively detectable.

The service maintains its own persistent ban list using NATS JetStream KeyValue stores, enabling moderators to take action against users even when they are offline. It automatically enforces bans when users rejoin, performs IP correlation to detect ban evasion across accounts, and provides username pattern matching for instant action against bad actors (e.g., usernames containing hate symbols or slurs).

The service integrates with `kryten-cli` for command-line moderation actions and will expose the same commands via private messages to authorized channel moderators (rank 2+).

## 2. Goals

### 2.1 Business goals

- Reduce moderator burden by automating repetitive moderation tasks
- Provide persistent ban enforcement that survives user reconnection
- Enable proactive moderation against known bad actors and ban evaders
- Create a foundation for future advanced moderation features

### 2.2 User goals

- Moderators can ban/silence users regardless of online status
- Moderators can view and manage the ban list via CLI
- Automatic enforcement of bans when users rejoin
- Automatic detection and action against IP-based ban evasion
- Instant action against usernames matching configured patterns

### 2.3 Non-goals

- This service does not replace CyTube's native moderation (it supplements it)
- This service does not provide content moderation (message filtering) in v1.0
- This service does not integrate with external moderation databases
- This service does not provide rate limiting or spam detection in v1.0

## 3. User personas

### 3.1 Key user types

- **Channel Moderators**: Users with rank 2+ who need to moderate chat
- **Channel Administrators**: Users with rank 3+ who manage moderation policies
- **System Operators**: Operators managing the Kryten infrastructure (whitelist by username)

### 3.2 Basic persona details

- **Moderator (rank 2)**: Can ban/unban users, view moderation list, smute/mute users.
- **Channel Admin (rank 3 or whitelist)**: Can configure banned username patterns, manage moderator access.
- **System Operator**: Configures the service, monitors metrics, manages infrastructure.

### 3.3 Role-based access

- **Rank 0-1 (Guest/Member)**: No moderation access
- **Rank 2 (Moderator)**: Can use all moderation commands (ban, unban, smute, unsmute, mute, unmute, list)
- **Rank 3+ or whitelisted (Admin)**: Can configure patterns, view detailed logs, manage service settings

## 4. Functional requirements

### 4.1 Persistent ban list (Priority: High)

- Service maintains a moderation list in NATS JetStream KV store
- Entries include: username, action (ban/mute/smute), reason, added_by, timestamp, IP addresses
- Action types: `ban` (kick on join), `mute` (visible mute), `smute` (shadow mute - user doesn't know)
- Moderation list persists across service restarts
- List is keyed by lowercase username for case-insensitive matching

### 4.2 Automatic enforcement on join (Priority: High)

- On `adduser` event, check if username exists in moderation list
- If user is banned: execute kick/ban command via kryten-py
- If user is smuted: execute shadow mute command via kryten-py (user unaware)
- If user is muted: execute mute command via kryten-py (user notified)
- Shadow mute (smute) is the preferred enforcement for non-hate-related bad actors
- Log all enforcement actions with timestamp and reason

### 4.3 IP correlation for ban evasion detection (Priority: High)

- On `adduser` event, extract IP from user metadata (if available)
- Check if IP matches any existing banned user's IP
- If match found: apply the same action to the new account
- Store the IP correlation for audit purposes
- Support both full IP and masked IP matching

### 4.4 Username pattern matching (Priority: High)

- Configurable list of banned patterns (substrings, regex)
- On `adduser` event, check username against all patterns
- If match found: execute immediate ban with pattern as reason
- Default patterns for known hate symbols (configurable)
- Patterns stored in KV store for persistence

### 4.5 CLI commands for moderation (Priority: High)

- `kryten moderator ban <username> [reason]` - Add user to ban list (kicks on join)
- `kryten moderator unban <username>` - Remove user from ban list
- `kryten moderator smute <username> [reason]` - Shadow mute user (they don't know they're muted)
- `kryten moderator unsmute <username>` - Remove shadow mute from user
- `kryten moderator mute <username> [reason]` - Visible mute user (they see notification)
- `kryten moderator unmute <username>` - Remove visible mute from user
- `kryten moderator list [--filter ban|mute|smute]` - Show moderation list
- `kryten moderator check <username>` - Check if user is in list
- `kryten moderator patterns list` - List banned username patterns
- `kryten moderator patterns add <pattern>` - Add pattern
- `kryten moderator patterns remove <pattern>` - Remove pattern

### 4.6 NATS command interface (Priority: High)

- Single command subject: `kryten.moderator.command`
- Request/response pattern with JSON payloads
- All CLI commands route through this interface
- Standard response format: `{success: bool, data: {...}, error: string}`

## 5. User experience

### 5.1 Entry points and first-time user flow

- System operators install via `pipx install kryten-moderator`
- Configure `config.json` with NATS and channel settings
- Service auto-connects and begins monitoring on startup
- Moderators interact via CLI immediately

### 5.2 Core experience

- **Monitoring**: Service silently monitors all user joins
- **Detection**: Automatic checks against ban list, IP correlation, patterns
- **Enforcement**: Immediate action taken without moderator intervention
- **Manual control**: Moderators add/remove entries as needed

### 5.3 Advanced features and edge cases

- IP may not be available for all users (depends on CyTube config)
- Masked IPs provide partial matching (same ISP/region detection)
- Pattern matching is case-insensitive
- Multiple moderators can manage the list concurrently
- Conflict resolution: most recent action wins

### 5.4 UI/UX highlights

- CLI provides colored output for ban/mute status
- List commands support filtering and pagination
- All actions logged with timestamps for audit

## 6. Narrative

A moderator notices a user "SubtleTroll" making passive-aggressive comments that don't quite cross the line but disrupt the chat. They issue `kryten moderator smute SubtleTroll "Passive-aggressive behavior"` from the CLI. The service adds the user to the moderation list with action=smute, stores their current IP, and shadow mutes them if online. SubtleTroll continues typing but only moderators see their messages - they have no idea they've been muted.

A more egregious user "TrollAccount123" is harassing others. They issue `kryten moderator ban TrollAccount123 "Harassment"` from the CLI. The service adds them to the ban list, stores their IP, and kicks them if online.

Later, TrollAccount123 creates "TrollAccount456" from the same IP and joins the channel. The service detects the IP match, automatically applies the same action (ban), and logs the evasion detection. The moderator sees in the logs that ban evasion was detected and blocked.

Meanwhile, a user named "Hitler88_SS" joins. The pattern matcher detects "hitler" and "88" in the username and instantly bans the account before they can even send a message.


## 7. Success metrics

### 7.1 User-centric metrics

- Time to ban a user (< 1 second for automated, < 5 seconds for manual)
- False positive rate for pattern matching (< 1%)
- False positive rate for IP correlation (< 5%)

### 7.2 Business metrics

- Reduction in repeat offender incidents
- Moderator time saved on repetitive bans
- Coverage of known bad actor patterns

### 7.3 Technical metrics

- `moderator_bans_enforced` - Total automatic ban enforcements
- `moderator_smutes_enforced` - Total automatic shadow mute enforcements
- `moderator_mutes_enforced` - Total automatic visible mute enforcements
- `moderator_ip_correlations` - IP-based ban evasion detections
- `moderator_pattern_matches` - Username pattern matches
- `moderator_commands_processed` - CLI commands processed
- `moderator_list_size` - Current size of moderation list

## 8. Technical considerations

### 8.1 Integration points

- **kryten-py**: Core library for NATS, events, and KV store
- **kryten-cli**: Extended with `moderator` subcommand
- **Kryten-Robot**: Source of user events and userlist state
- **NATS JetStream**: KV store for persistent data
- **CyTube**: Target for kick/ban/mute/smute commands

### 8.2 Data storage and privacy

- Ban list stored in NATS KV bucket: `kryten_moderator_bans`
- Pattern list stored in NATS KV bucket: `kryten_moderator_patterns`
- IP addresses stored for correlation (consider GDPR implications)
- Audit log stored locally or in KV for review
- Data retention policy configurable

### 8.3 Scalability and performance

- Single instance per channel (no horizontal scaling needed)
- In-memory cache of ban list for fast lookups
- KV store for persistence and recovery
- Pattern matching uses compiled regex for performance

### 8.4 Potential challenges

- IP availability depends on CyTube server configuration
- Race conditions between multiple moderators (last write wins)
- Pattern matching false positives require tuning
- Rank verification for later PM interface requires fresh userlist data

## 9. Milestones and sequencing

### 9.1 Project estimate

- Medium: 2-3 weeks of development

### 9.2 Team size and composition

- 1 developer with Python/NATS experience
- 1 tester for moderation scenarios

### 9.3 Suggested phases

- **Phase 1**: Core ban list and enforcement (3-4 days)
  - KV store schema and persistence
  - Ban/unban/mute/unmute logic
  - Automatic enforcement on join
  - Basic CLI commands

- **Phase 2**: IP correlation (2-3 days)
  - IP extraction from user metadata
  - IP storage and matching logic
  - Masked IP support
  - Correlation logging

- **Phase 3**: Pattern matching (2-3 days)
  - Pattern storage and management
  - Regex compilation and matching
  - Default patterns configuration
  - Pattern CLI commands

- **Phase 4**: CLI integration (2-3 days)
  - kryten-cli moderator subcommand
  - All command implementations
  - Colored output and formatting
  - Help text and examples

- **Phase 5**: Testing and polish (2-3 days)
  - Integration testing
  - Edge case handling
  - Documentation
  - Metrics verification

## 10. User stories

### 10.1 Ban user via CLI

- **ID**: MOD-001
- **Description**: As a moderator, I want to ban a user via CLI so that they cannot rejoin the channel.
- **Acceptance criteria**:
  - Command `kryten moderator ban <username> [reason]` adds user to ban list
  - If user is currently online, they are kicked immediately
  - Ban entry includes: username, reason, banned_by, timestamp
  - Command returns success confirmation with ban details
  - Ban persists across service restarts

### 10.2 Unban user via CLI

- **ID**: MOD-002
- **Description**: As a moderator, I want to unban a user via CLI so that they can rejoin the channel.
- **Acceptance criteria**:
  - Command `kryten moderator unban <username>` removes user from ban list
  - Command returns success confirmation
  - If user was not banned, returns appropriate message
  - User can rejoin channel after unban

### 10.3 Shadow mute user via CLI

- **ID**: MOD-003
- **Description**: As a moderator, I want to shadow mute a user via CLI so that their messages are hidden without them knowing.
- **Acceptance criteria**:
  - Command `kryten moderator smute <username> [reason]` adds user to smute list
  - Shadow mute means user doesn't know they're muted (messages visible only to mods)
  - If user is currently online, smute is applied immediately via `/smute` command
  - Entry includes: username, action=smute, reason, added_by, timestamp
  - Command returns success confirmation
  - This is the preferred enforcement for non-hate-related bad actors

### 10.4 Remove shadow mute via CLI

- **ID**: MOD-004
- **Description**: As a moderator, I want to remove a shadow mute from a user via CLI so that their messages are visible again.
- **Acceptance criteria**:
  - Command `kryten moderator unsmute <username>` removes user from smute list
  - If user is currently online, unmute is applied immediately via `/unmute` command
  - Command returns success confirmation
  - If user was not smuted, returns appropriate message

### 10.4a Visible mute user via CLI

- **ID**: MOD-004a
- **Description**: As a moderator, I want to visibly mute a user via CLI so that they know they've been muted.
- **Acceptance criteria**:
  - Command `kryten moderator mute <username> [reason]` adds user to mute list
  - Visible mute means user is notified they are muted
  - If user is currently online, mute is applied immediately via `/mute` command
  - Entry includes: username, action=mute, reason, added_by, timestamp
  - Command returns success confirmation
  - Used less frequently than smute (mainly for explicit warnings)

### 10.4b Remove visible mute via CLI

- **ID**: MOD-004b
- **Description**: As a moderator, I want to remove a visible mute from a user via CLI so that they can chat again.
- **Acceptance criteria**:
  - Command `kryten moderator unmute <username>` removes user from mute list
  - If user is currently online, unmute is applied immediately via `/unmute` command
  - Command returns success confirmation
  - If user was not muted, returns appropriate message

### 10.5 List moderated users

- **ID**: MOD-005
- **Description**: As a moderator, I want to view the list of moderated users so that I can review current moderation state.
- **Acceptance criteria**:
  - Command `kryten moderator list` shows all moderated users
  - Optional filter: `--filter ban`, `--filter smute`, or `--filter mute`
  - Output includes: username, action (ban/smute/mute), reason, moderator, timestamp
  - Output is formatted as a table
  - Large lists support pagination

### 10.6 Check user moderation status

- **ID**: MOD-006
- **Description**: As a moderator, I want to check if a specific user is moderated so that I can see their status.
- **Acceptance criteria**:
  - Command `kryten moderator check <username>` shows user's moderation status
  - If moderated: shows action type, reason, moderator, timestamp, associated IPs
  - If not moderated: shows "User not found in moderation list"

### 10.7 Automatic ban enforcement on join

- **ID**: MOD-007
- **Description**: As a system, I want to automatically enforce bans when users join so that banned users cannot participate.
- **Acceptance criteria**:
  - On `adduser` event, service checks username against ban list (case-insensitive)
  - If banned: kick command is sent via kryten-py
  - Action is logged with timestamp and original ban reason
  - Metrics counter `moderator_bans_enforced` is incremented

### 10.8 Automatic smute enforcement on join

- **ID**: MOD-008
- **Description**: As a system, I want to automatically enforce shadow mutes when users join so that smuted users' messages are only visible to mods.
- **Acceptance criteria**:
  - On `adduser` event, service checks username against smute list (case-insensitive)
  - If smuted: shadow mute command is sent via kryten-py (`/smute`)
  - Action is logged with timestamp and original smute reason
  - Metrics counter `moderator_smutes_enforced` is incremented
  - User is not notified of their smute status

### 10.8a Automatic visible mute enforcement on join

- **ID**: MOD-008a
- **Description**: As a system, I want to automatically enforce visible mutes when users join so that muted users are notified.
- **Acceptance criteria**:
  - On `adduser` event, service checks username against mute list (case-insensitive)
  - If muted: mute command is sent via kryten-py (`/mute`)
  - Action is logged with timestamp and original mute reason
  - Metrics counter `moderator_mutes_enforced` is incremented
  - User receives notification they are muted

### 10.9 IP correlation detection

- **ID**: MOD-009
- **Description**: As a system, I want to detect ban evasion via IP correlation so that alternate accounts are also moderated.
- **Acceptance criteria**:
  - On `adduser` event, service extracts IP from user metadata
  - Service checks IP against all stored IPs in moderation list (ban/smute/mute)
  - If match found: apply same action to new account
  - New account is added to list with reference to original entry
  - Metrics counter `moderator_ip_correlations` is incremented
  - Log includes both usernames and IP (masked for privacy in output)

### 10.10 Username pattern matching

- **ID**: MOD-010
- **Description**: As a system, I want to automatically ban users with offensive usernames so that bad actors are blocked instantly.
- **Acceptance criteria**:
  - On `adduser` event, service checks username against configured patterns
  - Patterns can be substrings or regex
  - Matching is case-insensitive
  - If match found: instant ban with pattern as reason
  - Metrics counter `moderator_pattern_matches` is incremented

### 10.11 Manage banned patterns

- **ID**: MOD-011
- **Description**: As an admin, I want to manage the list of banned username patterns so that I can control automated bans.
- **Acceptance criteria**:
  - Command `kryten moderator patterns list` shows all patterns
  - Command `kryten moderator patterns add <pattern>` adds a pattern
  - Command `kryten moderator patterns remove <pattern>` removes a pattern
  - Patterns persist in KV store
  - Invalid regex patterns are rejected with error message

### 10.12 Store IP with moderation entry

- **ID**: MOD-012
- **Description**: As a system, I want to store IP addresses with moderation entries so that IP correlation can be performed.
- **Acceptance criteria**:
  - When user is banned/muted while online, their IP is stored
  - When user joins with matching IP, their new IP is also stored
  - IPs are stored as list to handle VPN/dynamic IP users
  - Both full IP and masked IP are stored if available

### 10.13 Persist moderation list to KV store

- **ID**: MOD-013
- **Description**: As a system, I want to persist the moderation list to NATS KV so that data survives restarts.
- **Acceptance criteria**:
  - Ban list stored in KV bucket `kryten_moderator_bans`
  - Pattern list stored in KV bucket `kryten_moderator_patterns`
  - Data is written on every add/remove operation
  - Data is loaded on service startup
  - KV bucket is created automatically if not exists

### 10.14 Health and metrics endpoints

- **ID**: MOD-014
- **Description**: As an operator, I want health and metrics endpoints so that I can monitor the service.
- **Acceptance criteria**:
  - `GET /health` returns JSON with service status
  - `GET /metrics` returns Prometheus format metrics
  - Metrics include all counters defined in section 7.3
  - Health includes ban list size and pattern count

---

## Data structures

### Moderation entry (KV value)

```json
{
  "username": "trolluser",
  "action": "smute",
  "reason": "Disruptive behavior",
  "moderator": "admin_user",
  "timestamp": "2025-12-14T10:30:00Z",
  "ips": ["192.168.1.x", "10.0.0.x"],
  "ip_correlation_source": null,
  "pattern_match": null
}
```

**Action values**: `ban` (kick on join), `smute` (shadow mute - user unaware), `mute` (visible mute)
```

### Pattern entry (KV value)

```json
{
  "pattern": "1488",
  "is_regex": false,
  "added_by": "admin_user",
  "timestamp": "2025-12-14T10:00:00Z"
}
```

### NATS command request format

```json
{
  "service": "moderator",
  "command": "ban.add",
  "username": "trolluser",
  "reason": "Harassment",
  "moderator": "admin_user"
}
```

### NATS command response format

```json
{
  "service": "moderator",
  "command": "ban.add",
  "success": true,
  "data": {
    "username": "trolluser",
    "action": "ban",
    "reason": "Harassment"
  }
}
```

---

## Configuration schema

```json
{
  "service": {
    "name": "moderator",
    "enable_lifecycle": true,
    "enable_heartbeat": true
  },
  "nats": {
    "servers": ["nats://localhost:4222"]
  },
  "channels": [
    {"domain": "cytu.be", "channel": "your_channel"}
  ],
  "metrics": {
    "port": 28284
  },
  "moderation": {
    "enable_auto_enforcement": true,
    "enable_ip_correlation": true,
    "enable_pattern_matching": true,
    "default_patterns": ["1488", "hitler", "88$"]
  },
  "kv_buckets": {
    "bans": "kryten_moderator_bans",
    "patterns": "kryten_moderator_patterns"
  }
}
```

---

After reviewing this PRD, please confirm if you would like to proceed with creating GitHub issues for the user stories.
