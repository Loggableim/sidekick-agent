"""
Discord Bot API — Backend handlers for the Discord WebUI plugin
"""
import json, os, datetime

def _j(handler, payload, status=200):
    """Send JSON response (inline helper, avoids Python 3.14 asyncio crash via api.helpers)"""
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
    handler.wfile.write(body)

GUILD_ID = '1367623607055810620'
BOT_ID = '1503402978843955293'

def _active_bot_config():
    try:
        from web.api.space_engine import get_active_workspace_slug, get_workspace
        slug = get_active_workspace_slug()
        if slug:
            ws = get_workspace(slug)
            if ws:
                discord = (ws.load_config() or {}).get("discord") or {}
                bots = discord.get("bots") or {}
                active = discord.get("active_bot") or next(iter(bots), None)
                cfg = bots.get(active) if active else None
                if isinstance(cfg, dict):
                    return cfg
    except Exception:
        pass
    return {}

def _active_guild_id():
    return str(_active_bot_config().get("guild_id") or GUILD_ID)

def _active_bot_id():
    return str(_active_bot_config().get("client_id") or BOT_ID)

def _get_headers():
    token = _active_bot_token()
    if not token:
        raise RuntimeError("Discord bot token is not configured")
    return {
        'Authorization': f'Bot {token}',
        'Content-Type': 'application/json',
        'User-Agent': 'SidekickWebUI/1.0 (https://github.com/Loggableim/sidekick)',
    }


def _active_bot_token():
    cfg = _active_bot_config()
    token = (cfg.get("token") or "").strip()
    if not token:
        token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if token:
        return token

    token_file = os.getenv("DISCORD_BOT_TOKEN_FILE", "").strip()
    if token_file:
        with open(token_file, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def _api(method, path, data=None):
    """Make Discord API call via urllib (stdlib, no requests dep)"""
    import urllib.request, urllib.error
    url = f'https://discord.com/api/v10{path}'
    hdrs = _get_headers()
    if data is not None:
        body = json.dumps(data).encode()
    else:
        body = None
    
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode()
            if text:
                return json.loads(text)
            return {'status': resp.status}
    except urllib.error.HTTPError as e:
        return {'error': True, 'status': e.code, 'message': e.read().decode()[:200]}
    except Exception as e:
        return {'error': True, 'message': str(e)}

def handle_get(handler, parsed):
    """Handle GET /api/discord/* endpoints"""
    path = parsed.path
    guild_id = _active_guild_id()
    
    if path == '/api/discord/guild':
        return _j(handler, _api('GET', f'/guilds/{guild_id}?with_counts=true'))
    
    if path == '/api/discord/channels':
        data = _api('GET', f'/guilds/{guild_id}/channels')
        if isinstance(data, list):
            return _j(handler, {'channels': data, 'total': len(data)})
        return _j(handler, data)
    
    if path == '/api/discord/roles':
        data = _api('GET', f'/guilds/{guild_id}/roles')
        if isinstance(data, list):
            return _j(handler, {'roles': data, 'total': len(data)})
        return _j(handler, data)
    
    if path == '/api/discord/members':
        data = _api('GET', f'/guilds/{guild_id}/members?limit=1000')
        if isinstance(data, list):
            bots = [m for m in data if m.get('user', {}).get('bot')]
            humans = [m for m in data if not m.get('user', {}).get('bot')]
            return _j(handler, {
                'members': data[:20],  # First 20 for preview
                'total': len(data),
                'bots': len(bots),
                'humans': len(humans),
                'bot_list': [{
                    'id': m['user']['id'],
                    'name': m['user']['username'],
                    'discriminator': m['user'].get('discriminator', ''),
                } for m in bots]
            })
        return _j(handler, data)
    
    if path == '/api/discord/stats':
        guild = _api('GET', f'/guilds/{guild_id}?with_counts=true')
        channels = _api('GET', f'/guilds/{guild_id}/channels')
        roles = _api('GET', f'/guilds/{guild_id}/roles')
        
        # If guild call failed, propagate error
        if isinstance(guild, dict) and guild.get('error'):
            return _j(handler, {'error': True, 'message': guild.get('message', 'Guild API error'), 'status': guild.get('status')})
        
        cat_count = len([c for c in (channels if isinstance(channels, list) else []) if c.get('type') == 4])
        text_count = len([c for c in (channels if isinstance(channels, list) else []) if c.get('type') in (0, 5, 15)])
        voice_count = len([c for c in (channels if isinstance(channels, list) else []) if c.get('type') == 2])
        
        return _j(handler, {
            'name': guild.get('name', '?'),
            'member_count': guild.get('approximate_member_count', guild.get('member_count', '?')),
            'online': guild.get('approximate_presence_count', '?'),
            'boosts': guild.get('premium_subscription_count', 0),
            'tier': guild.get('premium_tier', 0),
            'channels': {
                'total': len(channels) if isinstance(channels, list) else 0,
                'categories': cat_count,
                'text': text_count,
                'voice': voice_count
            },
            'roles': len(roles) if isinstance(roles, list) else 0,
            'features': guild.get('features', [])
        })
    
    if path.startswith('/api/discord/member/'):
        user_id = path.split('/')[-1]
        return _j(handler, _api('GET', f'/guilds/{guild_id}/members/{user_id}'))
    
    if path == '/api/discord/bot/info':
        app = _api('GET', '/applications/@me')
        return _j(handler, {
            'name': app.get('name', '?'),
            'id': app.get('id'),
            'icon': app.get('icon'),
            'bot_public': app.get('bot_public'),
            'flags': app.get('flags', 0),
            'bot_id': _active_bot_id(),
        })
    
    if path == '/api/discord/warns':
        warns_file = os.path.join(os.path.dirname(__file__), '..', 'pawsunitedbot', 'data', 'warns.json')
        if os.path.exists(warns_file):
            with open(warns_file) as f:
                return _j(handler, json.load(f))
        return _j(handler, {})
    
    # ── Debug endpoint: raw API responses ──
    if path == '/api/discord/debug':
        results = {}
        for label, method, api_path in [
            ('bot_me', 'GET', '/users/@me'),
            ('guild', 'GET', f'/guilds/{guild_id}?with_counts=true'),
            ('channels', 'GET', f'/guilds/{guild_id}/channels'),
            ('roles', 'GET', f'/guilds/{guild_id}/roles'),
            ('members_count', 'GET', f'/guilds/{guild_id}/members?limit=1'),
        ]:
            r = _api(method, api_path)
            if isinstance(r, dict) and r.get('error'):
                results[label] = {'error': r.get('message', '?'), 'status': r.get('status', '?')}
            elif isinstance(r, list):
                results[label] = {'type': 'list', 'count': len(r), 'first': r[0] if r else None}
            else:
                results[label] = r
        token_file = os.getenv("DISCORD_BOT_TOKEN_FILE", "").strip()
        return _j(handler, {'debug': results, 'token_file_exists': bool(token_file and os.path.exists(token_file)), 'guild_id': guild_id})
    
    # ── Raw test: full HTTP response details ──
    if path == '/api/discord/rawtest':
        import urllib.request, urllib.error
        token = _active_bot_token()
        if not token:
            return _j(handler, {'status': 0, 'error': 'Discord bot token is not configured'})
        url = 'https://discord.com/api/v10/users/@me'
        req = urllib.request.Request(url, headers={
            'Authorization': f'Bot {token}',
            'Content-Type': 'application/json',
            'User-Agent': 'SidekickWebUI/1.0',
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read()
                return _j(handler, {
                    'status': resp.status,
                    'headers': dict(resp.headers),
                    'body_preview': body.decode()[:500],
                })
        except urllib.error.HTTPError as e:
            body = e.read()
            return _j(handler, {
                'status': e.code,
                'headers': dict(e.headers),
                'body': body.decode(errors='replace')[:500],
                'error_details': {
                    'code': e.code,
                    'msg': str(e),
                }
            }, status=200)
        except Exception as e:
            return _j(handler, {'status': 0, 'error': str(e)})
    
    # ── Channel Tree: categories + channels hierarchisch ──
    if path == '/api/discord/channels/tree':
        raw = _api('GET', f'/guilds/{guild_id}/channels')
        if not isinstance(raw, list):
            return _j(handler, {'error': True, 'message': 'Failed to fetch channels'})
        
        categories = {}
        uncategorized = []
        for ch in raw:
            if ch.get('type') == 4:  # category
                categories[ch['id']] = {
                    'id': ch['id'],
                    'name': ch.get('name', 'Unnamed'),
                    'position': ch.get('position', 0),
                    'channels': []
                }
        
        for ch in raw:
            if ch.get('type') == 4:
                continue
            entry = {
                'id': ch['id'],
                'name': ch.get('name', 'Unnamed'),
                'type': ch.get('type', 0),
                'position': ch.get('position', 0),
                'topic': ch.get('topic', ''),
                'nsfw': ch.get('nsfw', False),
            }
            parent = ch.get('parent_id')
            if parent and parent in categories:
                categories[parent]['channels'].append(entry)
            else:
                uncategorized.append(entry)
        
        # Sort by position
        for cat in categories.values():
            cat['channels'].sort(key=lambda c: c.get('position', 0))
        sorted_cats = sorted(categories.values(), key=lambda c: c.get('position', 0))
        uncategorized.sort(key=lambda c: c.get('position', 0))
        
        return _j(handler, {
            'categories': sorted_cats,
            'uncategorized': uncategorized,
        })
    
    # ── Channel Messages ──
    if path.startswith('/api/discord/channel/') and path.endswith('/messages'):
        parts = path.split('/')
        # /api/discord/channel/{id}/messages
        try:
            ch_idx = parts.index('channel')
            channel_id = parts[ch_idx + 1]
        except (ValueError, IndexError):
            return _j(handler, {'error': True, 'message': 'Invalid channel path'})
        
        from urllib.parse import parse_qs
        qs = parse_qs(parsed.query)
        limit = int(qs.get('limit', ['50'])[0])
        before = qs.get('before', [None])[0]
        
        api_path = f'/channels/{channel_id}/messages?limit={min(limit, 100)}'
        if before:
            api_path += f'&before={before}'
        
        msgs = _api('GET', api_path)
        if isinstance(msgs, list):
            return _j(handler, {'messages': msgs, 'count': len(msgs)})
        return _j(handler, msgs)
    
    return _j(handler, {'error': True, 'message': f'Unknown endpoint: {path}'})

def handle_post(handler, parsed, body):
    """Handle POST /api/discord/* endpoints"""
    path = parsed.path
    b = body or {}
    guild_id = _active_guild_id()
    
    if path == '/api/discord/warn':
        user_id = b.get('user_id', '')
        reason = b.get('reason', 'Kein Grund')
        result = _api('GET', f'/guilds/{guild_id}/members/{user_id}')
        if result.get('error'):
            return _j(handler, result)
        warns_file = os.path.join(os.path.dirname(__file__), '..', 'pawsunitedbot', 'data', 'warns.json')
        warns = {}
        if os.path.exists(warns_file):
            with open(warns_file) as f:
                warns = json.load(f)
        guild_id_str = str(guild_id)
        if guild_id_str not in warns:
            warns[guild_id_str] = {}
        if user_id not in warns[guild_id_str]:
            warns[guild_id_str][user_id] = []
        warns[guild_id_str][user_id].append({
            'reason': reason,
            'moderator': 'WebUI',
            'time': datetime.datetime.utcnow().isoformat()
        })
        with open(warns_file, 'w') as f:
            json.dump(warns, f, indent=2)
        return _j(handler, {'status': 'ok', 'warn_count': len(warns[guild_id_str][user_id])})
    
    if path == '/api/discord/timeout':
        user_id = b.get('user_id', '')
        minutes = int(b.get('minutes', 10))
        reason = b.get('reason', 'Kein Grund')
        until = (datetime.datetime.utcnow() + datetime.timedelta(minutes=minutes)).isoformat()
        return _j(handler, _api('PATCH', f'/guilds/{guild_id}/members/{user_id}', {
            'communication_disabled_until': until
        }))
    
    if path == '/api/discord/untimeout':
        user_id = b.get('user_id', '')
        return _j(handler, _api('PATCH', f'/guilds/{guild_id}/members/{user_id}', {
            'communication_disabled_until': None
        }))
    
    if path == '/api/discord/kick':
        user_id = b.get('user_id', '')
        reason = b.get('reason', 'Kein Grund')
        return _j(handler, _api('DELETE', f'/guilds/{guild_id}/members/{user_id}'))
    
    if path == '/api/discord/ban':
        user_id = b.get('user_id', '')
        reason = b.get('reason', 'Kein Grund')
        days = int(b.get('delete_days', 0))
        return _j(handler, _api('PUT', f'/guilds/{guild_id}/bans/{user_id}', {
            'delete_message_days': days,
            'reason': reason
        }))
    
    if path == '/api/discord/unban':
        user_id = b.get('user_id', '')
        return _j(handler, _api('DELETE', f'/guilds/{guild_id}/bans/{user_id}'))
    
    if path == '/api/discord/config':
        config_file = os.path.join(os.path.dirname(__file__), '..', 'pawsunitedbot', 'data', 'config.json')
        default_config = {
            'welcome_enabled': True,
            'welcome_channel': 1459357082485522628,
            'level_enabled': True,
            'xp_per_message': 15,
            'auto_mod_enabled': True,
            'spam_threshold': 5,
            'spam_timeout_minutes': 10,
        }
        if b.get('action') == 'get':
            if os.path.exists(config_file):
                with open(config_file) as f:
                    cfg = json.load(f)
            else:
                cfg = {}
            for k, v in default_config.items():
                cfg.setdefault(k, v)
            return _j(handler, {'config': cfg})
        
        if b.get('action') == 'save':
            updates = b.get('values', {})
            if os.path.exists(config_file):
                with open(config_file) as f:
                    cfg = json.load(f)
            else:
                cfg = {}
            cfg.update(updates)
            with open(config_file, 'w') as f:
                json.dump(cfg, f, indent=2)
            return _j(handler, {'status': 'saved'})
    
    if path == '/api/discord/send':
        channel_id = b.get('channel_id', '')
        content = b.get('content', '')
        if not channel_id or not content:
            return _j(handler, {'error': True, 'message': 'channel_id and content required'})
        return _j(handler, _api('POST', f'/channels/{channel_id}/messages', {'content': content}))
    
    if path == '/api/discord/purge':
        channel_id = b.get('channel_id', '')
        amount = int(b.get('amount', 10))
        if amount > 100:
            amount = 100
        msgs = _api('GET', f'/channels/{channel_id}/messages?limit={amount}')
        if isinstance(msgs, list) and msgs:
            ids = [m['id'] for m in msgs if not m.get('pinned')]
            if ids:
                return _j(handler, _api('POST', f'/channels/{channel_id}/messages/bulk-delete', {'messages': ids[:100]}))
        return _j(handler, {'deleted': 0})
    
    return _j(handler, {'error': True, 'message': f'Unknown endpoint: {path}'})
