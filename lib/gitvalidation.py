'''Git repo validation with local command'''

import subprocess
from typing import Tuple, Final
import re

whitelist = [
    'abcdefghijklmnopqrstuvwxyz',
    'ABCDEFGHIJKLMNOPQRSTUVWXYZ',
    '0123456789',
    ':/.-_',
]


url = 'https://github.com/jleuth/inv.git'

def validateRepo(url: str) -> Tuple[bool, str]:
    '''Validate a repo with git ls-remote. Trying to avoid RCE by whitelisting characters, no interactive prompts, https enforcement, etc'''

    if not url or not isinstance(url, str):
        return False, 'URL is empty or not a string'

    if not url.startswith('https://'):
        return False, 'URL not HTTPS or doesnt start with https://'

    if len(url) > 512:
        return False, 'URL too long'

    pattern = (
        r'^https://[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?'
        r'(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*'
        r'(/[a-zA-Z0-9:/.\-_]*)?$'
    )
    if not re.match(pattern, url):
        return False, "Unrecognized URL format"

    attackMethods = { # this is only things possible with whitelisted symbols
        'flags': '--',
        'external': 'ext::',
        #ill put more here
    }
    for name, pattern in attackMethods:
        if pattern in url:
            return False, f"Blocked attack method '{name}'"

    # Check all characters in url against whitelist
    allowed_chars = set(''.join(whitelist))
    for char in url:
        if char not in allowed_chars:
            return False, 'URL contains invalid character'
    
    return True, ''

def runRepoCheck(url):
    '''Run validation THEN the command.'''

    isValid, error = validateRepo(url)
    if not isValid:
        return False, error

    envOptions = {
        'GIT_TERMINAL_PROMPT': '0',       # No credential prompts
        'GIT_ASKPASS': '/bin/true',       # Disable password asking
        'GIT_SSH_COMMAND': '/bin/false',  # Block SSH entirely
        'PATH': '/usr/bin:/bin',          # Restricted PATH
        'HOME': '/tmp',                   # Safe home directory
    }

    command = [
        'git',
        'ls-remote',
        '--exit-code',
        '--', # THIS MAKES SURE URL CANT BE A GIT OPTION
        url
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env = envOptions,
            shell=False, #ALSO CRITICAL
            timeout=15,
            cwd='/tmp',
        )

        if result.returncode == 0:
            return True, 'Valid repo'
        else:
            return False, f"Something went wrong: {result.stderr[:100]}" # truncate
        
    except subprocess.TimeoutExpired:
        return False, "Timeout on running git ls-remote"
    except Exception as e:
        return False, f"Error: {str(e)[:100]}"


    


