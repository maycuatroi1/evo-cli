---
name: socket-rpc-error-recovery
description: Recover true error from saved state when async worker socket closes before JSON reply
pattern_type: error_resolution
learned: true
learned_at: 2026-10-07T20:40:32
source_session: 111c1378-b0af-4cb4-89d6-e4bc3a0194ef
---

## When async worker hangs up

In client-worker patterns communicating over Unix domain sockets, if the worker exits and writes error state to disk, the client may receive an empty reply `b''` before the socket fully closes. Calling `json.loads(b'')` then fails with "Expecting value: line 1 column 1 (char 0)", which masks the actual error the worker saved to disk.

This happens because:
1. Worker catches exception, writes state file with error message
2. Worker starts to exit, closes socket file descriptor
3. Client sends request, gets queued in kernel backlog
4. Client tries to read reply from now-closed socket → recv returns empty bytes
5. JSON decode on empty bytes fails with cryptic error, hiding the real error

## How to fix

1. **Worker**: Save error state to file (e.g., `~/.worker-id/state.json`) before shutdown, not just in the final message
   ```python
   def save_state(profile, error=None):
       state = {"error": str(error)} if error else {"state": "ok"}
       Path(f"~/.worker/{hashlib.sha256(profile.encode()).hexdigest()[:12]}.json").write_text(json.dumps(state))
   ```

2. **Client**: When receiving empty reply or socket error, read the saved state file instead of crashing on empty JSON
   ```python
   def request(cmd):
       try:
           sock.sendall(cmd.encode())
           reply = sock.recv(4096)
           if not reply:
               return read_saved_state()  # socket closed before reply
           return json.loads(reply)
       except (BrokenPipeError, ConnectionResetError):
           return read_saved_state()  # socket error → fallback to saved state
       except json.JSONDecodeError:
           return read_saved_state()  # corrupted JSON → fallback to saved state
   
   def read_saved_state():
       try:
           return json.loads(Path(state_file).read_text())
       except:
           return {"error": "worker crashed, no saved state"}
   ```

3. **Test**: Add regression test that kills worker with request in-flight to verify client reports the true error, not JSONDecodeError

## Why

Socket close during reply results in confusing JSON error that hides the real reason (auth failed, network down, etc.). State file is the source of truth for async workers. Test suite should verify error recovery even under race conditions.

## Example scenario

VPN auth fails → worker writes `{"state": "FAILED", "error": "VPN authentication rejected"}` → worker exits → client request arrives after socket close → client would crash with "Expecting value" instead of showing the true auth error.
