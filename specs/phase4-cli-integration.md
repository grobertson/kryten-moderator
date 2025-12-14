# Phase 4: CLI Integration

**Duration**: 2-3 days  
**PRD Reference**: Section 9.3, Phase 4  
**User Stories**: MOD-001 through MOD-006, MOD-011  
**Depends On**: Phase 1-3 (All NATS commands implemented)

## Overview

Phase 4 adds the `kryten moderator` subcommand to kryten-cli, providing a user-friendly interface for all moderation operations. The CLI commands communicate with kryten-moderator via NATS request/reply, using the command interface established in previous phases.

## Deliverables

1. **Moderator Subcommand Group**
   - `kryten moderator ban <username> [reason]`
   - `kryten moderator unban <username>`
   - `kryten moderator smute <username> [reason]`
   - `kryten moderator unsmute <username>`
   - `kryten moderator mute <username> [reason]`
   - `kryten moderator unmute <username>`
   - `kryten moderator list [--filter ACTION]`
   - `kryten moderator check <username>`

2. **Pattern Subcommand Group**
   - `kryten moderator patterns list`
   - `kryten moderator patterns add <pattern> [--regex] [--action ACTION]`
   - `kryten moderator patterns remove <pattern>`

3. **Output Formatting**
   - Colored output for action types (ban=red, smute=yellow, mute=orange)
   - Table formatting for list output
   - Success/error messages with color coding
   - JSON output option for scripting

4. **Help Text and Examples**
   - Comprehensive --help for all commands
   - Usage examples in docstrings

---

## Technical Specification

### 1. NATS Command Subject

The moderator CLI communicates with kryten-moderator via:

**Subject**: `kryten.moderator.command`

All requests are JSON-encoded, responses are also JSON with `success` boolean.

### 2. CLI Command Implementation

#### Add to `kryten_cli.py`

```python
# Add color constants at top of file
class Colors:
    """ANSI color codes for terminal output."""
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    ORANGE = "\033[38;5;208m"
    BLUE = "\033[94m"
    RESET = "\033[0m"
    BOLD = "\033[1m"

# Add to KrytenCLI class

class KrytenCLI:
    # ... existing init and methods ...
    
    # ========================================================================
    # Moderator Commands
    # ========================================================================
    
    async def _moderator_request(self, command: str, **kwargs) -> dict:
        """Send a request to kryten-moderator and return the response.
        
        Args:
            command: Command name (e.g., "entry.add")
            **kwargs: Additional command parameters
            
        Returns:
            Response dict with success and data/error
        """
        request = {
            "service": "moderator",
            "command": command,
            **kwargs,
        }
        
        try:
            response = await self.client.request(
                "kryten.moderator.command",
                json.dumps(request).encode(),
                timeout=5.0,
            )
            return json.loads(response.data.decode())
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _format_action(self, action: str) -> str:
        """Format action with color."""
        colors = {
            "ban": Colors.RED,
            "smute": Colors.YELLOW,
            "mute": Colors.ORANGE,
        }
        color = colors.get(action, "")
        return f"{color}{action}{Colors.RESET}"
    
    async def cmd_moderator_ban(
        self,
        username: str,
        reason: str | None = None,
    ) -> None:
        """Add user to ban list.
        
        Args:
            username: Username to ban
            reason: Optional reason for the ban
        """
        response = await self._moderator_request(
            "entry.add",
            username=username,
            action="ban",
            reason=reason,
            moderator="cli",
        )
        
        if response.get("success"):
            data = response.get("data", {})
            print(f"{Colors.GREEN}✓{Colors.RESET} Banned {Colors.BOLD}{data['username']}{Colors.RESET}")
            if data.get("reason"):
                print(f"  Reason: {data['reason']}")
        else:
            print(f"{Colors.RED}✗ Error:{Colors.RESET} {response.get('error')}", file=sys.stderr)
            sys.exit(1)
    
    async def cmd_moderator_unban(self, username: str) -> None:
        """Remove user from ban list.
        
        Args:
            username: Username to unban
        """
        response = await self._moderator_request(
            "entry.remove",
            username=username,
        )
        
        if response.get("success"):
            print(f"{Colors.GREEN}✓{Colors.RESET} Unbanned {Colors.BOLD}{username}{Colors.RESET}")
        else:
            print(f"{Colors.RED}✗ Error:{Colors.RESET} {response.get('error')}", file=sys.stderr)
            sys.exit(1)
    
    async def cmd_moderator_smute(
        self,
        username: str,
        reason: str | None = None,
    ) -> None:
        """Shadow mute a user (they don't know they're muted).
        
        Args:
            username: Username to shadow mute
            reason: Optional reason
        """
        response = await self._moderator_request(
            "entry.add",
            username=username,
            action="smute",
            reason=reason,
            moderator="cli",
        )
        
        if response.get("success"):
            data = response.get("data", {})
            print(f"{Colors.GREEN}✓{Colors.RESET} Shadow muted {Colors.BOLD}{data['username']}{Colors.RESET}")
            if data.get("reason"):
                print(f"  Reason: {data['reason']}")
        else:
            print(f"{Colors.RED}✗ Error:{Colors.RESET} {response.get('error')}", file=sys.stderr)
            sys.exit(1)
    
    async def cmd_moderator_unsmute(self, username: str) -> None:
        """Remove shadow mute from user.
        
        Args:
            username: Username to unshadow mute
        """
        response = await self._moderator_request(
            "entry.remove",
            username=username,
        )
        
        if response.get("success"):
            print(f"{Colors.GREEN}✓{Colors.RESET} Removed shadow mute from {Colors.BOLD}{username}{Colors.RESET}")
        else:
            print(f"{Colors.RED}✗ Error:{Colors.RESET} {response.get('error')}", file=sys.stderr)
            sys.exit(1)
    
    async def cmd_moderator_mute(
        self,
        username: str,
        reason: str | None = None,
    ) -> None:
        """Visible mute a user (they are notified).
        
        Args:
            username: Username to mute
            reason: Optional reason
        """
        response = await self._moderator_request(
            "entry.add",
            username=username,
            action="mute",
            reason=reason,
            moderator="cli",
        )
        
        if response.get("success"):
            data = response.get("data", {})
            print(f"{Colors.GREEN}✓{Colors.RESET} Muted {Colors.BOLD}{data['username']}{Colors.RESET}")
            if data.get("reason"):
                print(f"  Reason: {data['reason']}")
        else:
            print(f"{Colors.RED}✗ Error:{Colors.RESET} {response.get('error')}", file=sys.stderr)
            sys.exit(1)
    
    async def cmd_moderator_unmute(self, username: str) -> None:
        """Remove visible mute from user.
        
        Args:
            username: Username to unmute
        """
        response = await self._moderator_request(
            "entry.remove",
            username=username,
        )
        
        if response.get("success"):
            print(f"{Colors.GREEN}✓{Colors.RESET} Unmuted {Colors.BOLD}{username}{Colors.RESET}")
        else:
            print(f"{Colors.RED}✗ Error:{Colors.RESET} {response.get('error')}", file=sys.stderr)
            sys.exit(1)
    
    async def cmd_moderator_list(
        self,
        filter_action: str | None = None,
        format: str = "table",
    ) -> None:
        """List all moderated users.
        
        Args:
            filter_action: Optional filter (ban, smute, mute)
            format: Output format (table or json)
        """
        response = await self._moderator_request(
            "entry.list",
            filter=filter_action,
        )
        
        if not response.get("success"):
            print(f"{Colors.RED}✗ Error:{Colors.RESET} {response.get('error')}", file=sys.stderr)
            sys.exit(1)
        
        data = response.get("data", {})
        entries = data.get("entries", [])
        
        if format == "json":
            print(json.dumps(data, indent=2))
            return
        
        if not entries:
            print("No moderation entries found.")
            return
        
        # Print header
        print(f"\n{Colors.BOLD}Moderation List{Colors.RESET}")
        print(f"Total entries: {data.get('count', len(entries))}")
        print("-" * 80)
        print(f"{'Username':<20} {'Action':<10} {'Reason':<25} {'Moderator':<15}")
        print("-" * 80)
        
        for entry in entries:
            action_colored = self._format_action(entry['action'])
            reason = (entry.get('reason') or '')[:23]
            if len(entry.get('reason') or '') > 23:
                reason += '..'
            moderator = (entry.get('moderator') or '')[:13]
            
            print(f"{entry['username']:<20} {action_colored:<19} {reason:<25} {moderator:<15}")
        
        print("-" * 80)
    
    async def cmd_moderator_check(
        self,
        username: str,
        format: str = "text",
    ) -> None:
        """Check moderation status of a user.
        
        Args:
            username: Username to check
            format: Output format (text or json)
        """
        response = await self._moderator_request(
            "entry.get",
            username=username,
        )
        
        if not response.get("success"):
            print(f"{Colors.RED}✗ Error:{Colors.RESET} {response.get('error')}", file=sys.stderr)
            sys.exit(1)
        
        data = response.get("data", {})
        
        if format == "json":
            print(json.dumps(data, indent=2))
            return
        
        if not data.get("moderated"):
            print(f"User {Colors.BOLD}{username}{Colors.RESET} is {Colors.GREEN}not moderated{Colors.RESET}")
            return
        
        action_colored = self._format_action(data['action'])
        print(f"\n{Colors.BOLD}Moderation Status: {data['username']}{Colors.RESET}")
        print("-" * 40)
        print(f"  Action:    {action_colored}")
        print(f"  Reason:    {data.get('reason') or '(none)'}")
        print(f"  Moderator: {data.get('moderator')}")
        print(f"  Timestamp: {data.get('timestamp')}")
        
        if data.get('ips'):
            # Mask IPs for privacy
            masked = [self._mask_ip(ip) for ip in data['ips']]
            print(f"  IPs:       {', '.join(masked)}")
        
        if data.get('ip_correlation_source'):
            print(f"  Correlated from: {data['ip_correlation_source']}")
        
        print("-" * 40)
    
    @staticmethod
    def _mask_ip(ip: str) -> str:
        """Mask IP for display."""
        parts = ip.split('.')
        if len(parts) >= 2:
            return f"{parts[0]}.{parts[1]}.x.x"
        return ip
    
    # ========================================================================
    # Pattern Commands
    # ========================================================================
    
    async def cmd_moderator_patterns_list(self, format: str = "table") -> None:
        """List all banned username patterns.
        
        Args:
            format: Output format (table or json)
        """
        response = await self._moderator_request("patterns.list")
        
        if not response.get("success"):
            print(f"{Colors.RED}✗ Error:{Colors.RESET} {response.get('error')}", file=sys.stderr)
            sys.exit(1)
        
        data = response.get("data", {})
        patterns = data.get("patterns", [])
        
        if format == "json":
            print(json.dumps(data, indent=2))
            return
        
        if not patterns:
            print("No patterns configured.")
            return
        
        print(f"\n{Colors.BOLD}Banned Username Patterns{Colors.RESET}")
        print(f"Total patterns: {data.get('count', len(patterns))}")
        print("-" * 80)
        print(f"{'Pattern':<25} {'Type':<10} {'Action':<10} {'Description':<30}")
        print("-" * 80)
        
        for p in patterns:
            ptype = "regex" if p.get('is_regex') else "substring"
            action_colored = self._format_action(p['action'])
            desc = (p.get('description') or '')[:28]
            if len(p.get('description') or '') > 28:
                desc += '..'
            
            print(f"{p['pattern']:<25} {ptype:<10} {action_colored:<19} {desc:<30}")
        
        print("-" * 80)
    
    async def cmd_moderator_patterns_add(
        self,
        pattern: str,
        is_regex: bool = False,
        action: str = "ban",
        description: str | None = None,
    ) -> None:
        """Add a banned username pattern.
        
        Args:
            pattern: Pattern string (substring or regex)
            is_regex: Whether pattern is a regex
            action: Action to take on match (ban, smute, mute)
            description: Optional description
        """
        response = await self._moderator_request(
            "patterns.add",
            pattern=pattern,
            is_regex=is_regex,
            action=action,
            added_by="cli",
            description=description,
        )
        
        if response.get("success"):
            data = response.get("data", {})
            ptype = "regex" if data.get('is_regex') else "substring"
            action_colored = self._format_action(data['action'])
            print(f"{Colors.GREEN}✓{Colors.RESET} Added pattern: {Colors.BOLD}{data['pattern']}{Colors.RESET}")
            print(f"  Type: {ptype}, Action: {action_colored}")
        else:
            print(f"{Colors.RED}✗ Error:{Colors.RESET} {response.get('error')}", file=sys.stderr)
            sys.exit(1)
    
    async def cmd_moderator_patterns_remove(self, pattern: str) -> None:
        """Remove a banned username pattern.
        
        Args:
            pattern: Pattern string to remove
        """
        response = await self._moderator_request(
            "patterns.remove",
            pattern=pattern,
        )
        
        if response.get("success"):
            print(f"{Colors.GREEN}✓{Colors.RESET} Removed pattern: {Colors.BOLD}{pattern}{Colors.RESET}")
        else:
            print(f"{Colors.RED}✗ Error:{Colors.RESET} {response.get('error')}", file=sys.stderr)
            sys.exit(1)
```

### 3. Argument Parser Updates

Add moderator subcommand group to the argument parser:

```python
def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Kryten CLI - Send CyTube commands via NATS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    # ... existing global options ...
    
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # ... existing subparsers ...
    
    # ========================================================================
    # Moderator Commands
    # ========================================================================
    
    moderator_parser = subparsers.add_parser(
        "moderator",
        help="Moderation commands (persistent ban/mute list)",
        description="Manage the persistent moderation list for automatic enforcement.",
    )
    moderator_sub = moderator_parser.add_subparsers(dest="moderator_cmd")
    
    # moderator ban
    mod_ban = moderator_sub.add_parser(
        "ban",
        help="Add user to ban list (kicks on join)",
        description="Add a user to the persistent ban list. They will be kicked whenever they try to join.",
    )
    mod_ban.add_argument("username", help="Username to ban")
    mod_ban.add_argument("reason", nargs="?", help="Reason for ban (optional)")
    
    # moderator unban
    mod_unban = moderator_sub.add_parser(
        "unban",
        help="Remove user from ban list",
    )
    mod_unban.add_argument("username", help="Username to unban")
    
    # moderator smute
    mod_smute = moderator_sub.add_parser(
        "smute",
        help="Shadow mute user (they don't know they're muted)",
        description="Shadow mute a user. Their messages will only be visible to moderators. The user is not notified.",
    )
    mod_smute.add_argument("username", help="Username to shadow mute")
    mod_smute.add_argument("reason", nargs="?", help="Reason for mute (optional)")
    
    # moderator unsmute
    mod_unsmute = moderator_sub.add_parser(
        "unsmute",
        help="Remove shadow mute from user",
    )
    mod_unsmute.add_argument("username", help="Username to unshadow mute")
    
    # moderator mute
    mod_mute = moderator_sub.add_parser(
        "mute",
        help="Visible mute user (they are notified)",
        description="Mute a user with notification. They will see they've been muted.",
    )
    mod_mute.add_argument("username", help="Username to mute")
    mod_mute.add_argument("reason", nargs="?", help="Reason for mute (optional)")
    
    # moderator unmute
    mod_unmute = moderator_sub.add_parser(
        "unmute",
        help="Remove visible mute from user",
    )
    mod_unmute.add_argument("username", help="Username to unmute")
    
    # moderator list
    mod_list = moderator_sub.add_parser(
        "list",
        help="List all moderated users",
    )
    mod_list.add_argument(
        "--filter",
        choices=["ban", "smute", "mute"],
        help="Filter by action type",
    )
    mod_list.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    
    # moderator check
    mod_check = moderator_sub.add_parser(
        "check",
        help="Check moderation status of a user",
    )
    mod_check.add_argument("username", help="Username to check")
    mod_check.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    
    # moderator patterns
    mod_patterns = moderator_sub.add_parser(
        "patterns",
        help="Manage banned username patterns",
    )
    patterns_sub = mod_patterns.add_subparsers(dest="patterns_cmd")
    
    # patterns list
    pat_list = patterns_sub.add_parser(
        "list",
        help="List all patterns",
    )
    pat_list.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format",
    )
    
    # patterns add
    pat_add = patterns_sub.add_parser(
        "add",
        help="Add a pattern",
    )
    pat_add.add_argument("pattern", help="Pattern string")
    pat_add.add_argument(
        "--regex",
        action="store_true",
        help="Treat pattern as regex (default: substring)",
    )
    pat_add.add_argument(
        "--action",
        choices=["ban", "smute", "mute"],
        default="ban",
        help="Action to take on match (default: ban)",
    )
    pat_add.add_argument(
        "--description",
        "-d",
        help="Description of pattern",
    )
    
    # patterns remove
    pat_remove = patterns_sub.add_parser(
        "remove",
        help="Remove a pattern",
    )
    pat_remove.add_argument("pattern", help="Pattern string to remove")
    
    return parser
```

### 4. Command Routing

Add routing logic in `main()`:

```python
async def main() -> None:
    # ... existing setup ...
    
    # Route moderator commands
    elif args.command == "moderator":
        if args.moderator_cmd == "ban":
            await cli.cmd_moderator_ban(args.username, args.reason)
        elif args.moderator_cmd == "unban":
            await cli.cmd_moderator_unban(args.username)
        elif args.moderator_cmd == "smute":
            await cli.cmd_moderator_smute(args.username, args.reason)
        elif args.moderator_cmd == "unsmute":
            await cli.cmd_moderator_unsmute(args.username)
        elif args.moderator_cmd == "mute":
            await cli.cmd_moderator_mute(args.username, args.reason)
        elif args.moderator_cmd == "unmute":
            await cli.cmd_moderator_unmute(args.username)
        elif args.moderator_cmd == "list":
            await cli.cmd_moderator_list(
                filter_action=args.filter,
                format=args.format,
            )
        elif args.moderator_cmd == "check":
            await cli.cmd_moderator_check(
                username=args.username,
                format=args.format,
            )
        elif args.moderator_cmd == "patterns":
            if args.patterns_cmd == "list":
                await cli.cmd_moderator_patterns_list(format=args.format)
            elif args.patterns_cmd == "add":
                await cli.cmd_moderator_patterns_add(
                    pattern=args.pattern,
                    is_regex=args.regex,
                    action=args.action,
                    description=args.description,
                )
            elif args.patterns_cmd == "remove":
                await cli.cmd_moderator_patterns_remove(args.pattern)
            else:
                parser.parse_args(["moderator", "patterns", "--help"])
        else:
            parser.parse_args(["moderator", "--help"])
```

---

## Usage Examples

### User Moderation

```bash
# Ban a user
$ kryten moderator ban TrollUser "Harassment"
✓ Banned TrollUser
  Reason: Harassment

# Shadow mute (user doesn't know)
$ kryten moderator smute SubtleTroll "Passive aggressive"
✓ Shadow muted SubtleTroll
  Reason: Passive aggressive

# Check user status
$ kryten moderator check TrollUser
Moderation Status: TrollUser
----------------------------------------
  Action:    ban
  Reason:    Harassment
  Moderator: cli
  Timestamp: 2025-12-14T10:30:00Z
  IPs:       192.168.x.x
----------------------------------------

# List all moderated users
$ kryten moderator list

Moderation List
Total entries: 3
--------------------------------------------------------------------------------
Username             Action     Reason                    Moderator      
--------------------------------------------------------------------------------
TrollUser            ban        Harassment                cli            
SubtleTroll          smute      Passive aggressive        cli            
SpamBot              ban        IP correlation with Tro.. system:ip_corr..
--------------------------------------------------------------------------------

# List only bans
$ kryten moderator list --filter ban

# Remove moderation
$ kryten moderator unban TrollUser
✓ Unbanned TrollUser
```

### Pattern Management

```bash
# List patterns
$ kryten moderator patterns list

Banned Username Patterns
Total patterns: 3
--------------------------------------------------------------------------------
Pattern                   Type       Action     Description                   
--------------------------------------------------------------------------------
1488                      substring  ban        Nazi hate number              
hitler                    substring  ban        Hitler reference              
^troll\d+$                regex      smute      Troll with numbers            
--------------------------------------------------------------------------------

# Add substring pattern
$ kryten moderator patterns add "badword" --action ban --description "Bad word filter"
✓ Added pattern: badword
  Type: substring, Action: ban

# Add regex pattern
$ kryten moderator patterns add "^evil.*$" --regex --action smute
✓ Added pattern: ^evil.*$
  Type: regex, Action: smute

# Remove pattern
$ kryten moderator patterns remove "badword"
✓ Removed pattern: badword
```

### JSON Output (for scripting)

```bash
# Get list as JSON
$ kryten moderator list --format json
{
  "count": 2,
  "entries": [
    {
      "username": "TrollUser",
      "action": "ban",
      "reason": "Harassment",
      "moderator": "cli",
      "timestamp": "2025-12-14T10:30:00Z"
    }
  ]
}

# Check status as JSON
$ kryten moderator check TrollUser --format json
{
  "username": "TrollUser",
  "moderated": true,
  "action": "ban",
  "reason": "Harassment",
  "moderator": "cli",
  "timestamp": "2025-12-14T10:30:00Z",
  "ips": ["192.168.1.x"]
}
```

---

## File Changes Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `kryten_cli.py` | **MODIFY** | Add Colors class, moderator methods, parser updates |
| `pyproject.toml` | **MODIFY** | Bump version to 2.4.0 |

---

## Testing Checklist

### Unit Tests

- [ ] `_moderator_request()` sends correct JSON to NATS
- [ ] `_format_action()` returns correct colors
- [ ] `_mask_ip()` properly masks IPs
- [ ] All cmd_moderator_* methods handle success response
- [ ] All cmd_moderator_* methods handle error response

### Integration Tests

- [ ] `kryten moderator ban <user>` creates entry
- [ ] `kryten moderator smute <user>` creates smute entry
- [ ] `kryten moderator list` shows all entries
- [ ] `kryten moderator check <user>` shows correct status
- [ ] `kryten moderator patterns list` shows all patterns
- [ ] `kryten moderator patterns add <pattern>` creates pattern
- [ ] Error from moderator service displayed correctly
- [ ] --format json produces valid JSON output

### Manual Testing

- [ ] Ban a user, verify output formatting
- [ ] Smute a user, verify yellow color
- [ ] List entries with filter, verify correct filtering
- [ ] Check moderated user, verify all fields displayed
- [ ] Check non-moderated user, verify "not moderated" message
- [ ] Add regex pattern, verify success
- [ ] Add invalid regex, verify error message
- [ ] Colors display correctly in terminal

---

## Help Output

```
$ kryten moderator --help
usage: kryten moderator [-h] {ban,unban,smute,unsmute,mute,unmute,list,check,patterns} ...

Manage the persistent moderation list for automatic enforcement.

positional arguments:
  {ban,unban,smute,unsmute,mute,unmute,list,check,patterns}
    ban                 Add user to ban list (kicks on join)
    unban               Remove user from ban list
    smute               Shadow mute user (they don't know they're muted)
    unsmute             Remove shadow mute from user
    mute                Visible mute user (they are notified)
    unmute              Remove visible mute from user
    list                List all moderated users
    check               Check moderation status of a user
    patterns            Manage banned username patterns

optional arguments:
  -h, --help            show this help message and exit
```

---

## Dependencies

- kryten-moderator running and connected to NATS
- kryten-py >= 0.9.4
- NATS server accessible

---

## Version

After completing Phase 4: `kryten-cli v2.4.0`
