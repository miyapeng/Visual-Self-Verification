"""Claude Code CLI adapter implementation for Vision2Web"""

import asyncio
import json
import shlex
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

from vision2web.inference.adapters.base import BaseAdapter
from vision2web.core.utils import build_claude_code_env, docker_env_flags


class ClaudeCodeAdapter(BaseAdapter):
    """Adapter that invokes Claude Code CLI via docker exec."""

    @property
    def framework_name(self) -> str:
        return "claude_code"

    async def run_task(
        self,
        workspace: Path,
        prompt: str,
        project_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        if not self.sandbox_manager:
            raise ValueError("Sandbox manager is required but not provided")

        start_time = datetime.now()
        logs = []
        status = 'failed'
        error_message = None

        try:
            container_id = self.sandbox_manager.get_container_id(workspace)
            if container_id is None:
                container_id = await self.sandbox_manager.create_container(workspace)
                if container_id is None:
                    raise Exception("Failed to create sandbox container")
                await self.sandbox_manager.start_container(workspace)

            env_flags = docker_env_flags(
                build_claude_code_env(
                    base_url=self.base_url,
                    api_key=self.api_key,
                    model=self.model,
                )
            )

            cmd = [
                "docker", "exec",
                "-w", "/workspace",
                *env_flags,
                container_id,
                "claude",
                "--print",
                "--verbose",
                "--output-format", "stream-json",
                "--dangerously-skip-permissions",
                "-p", prompt,
            ]

            self.logger.info(f"Running Claude Code CLI for {project_info['name']}...")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                if self.timeout:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(), timeout=self.timeout
                    )
                else:
                    stdout, stderr = await proc.communicate()
            except asyncio.TimeoutError:
                # Claude Code hung past the per-task limit. Kill the host-side
                # `docker exec` client, then kill the in-container claude process
                # so it does not linger as an orphan (which would keep the
                # container alive indefinitely with no API activity).
                self.logger.error(
                    f"Claude Code timed out after {self.timeout}s for "
                    f"{project_info['name']}; killing process."
                )
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
                await self._kill_container_claude(container_id)

                end_time = datetime.now()
                return {
                    'status': 'timeout',
                    'logs': logs + [f"Task timed out after {self.timeout}s"],
                    'conversation': [],
                    'error': f"Claude Code timed out after {self.timeout}s",
                    'start_time': start_time.isoformat(),
                    'end_time': end_time.isoformat(),
                    'duration': (end_time - start_time).total_seconds(),
                    'project_info': project_info,
                    'framework': self.framework_name,
                    'model': self.model,
                    'sandbox': True
                }

            stdout_text = stdout.decode('utf-8', errors='replace')
            stderr_text = stderr.decode('utf-8', errors='replace')

            # Parse stream-json to extract conversation messages
            conversation = []
            for line in stdout_text.splitlines():
                line = line.strip()
                if line:
                    try:
                        conversation.append(json.loads(line))
                    except json.JSONDecodeError:
                        conversation.append({"type": "raw", "content": line})

            logs.extend(stderr_text.splitlines() if stderr_text else [])

            if proc.returncode == 0:
                check_code, check_stdout, _ = await self.sandbox_manager.exec_command(
                    workspace,
                    "test -f /workspace/start.sh && echo 'EXISTS' || echo 'NOT_FOUND'"
                )

                if 'EXISTS' in check_stdout:
                    status = 'success'
                    self.logger.info(f"Task completed successfully for {project_info['name']}")
                else:
                    status = 'failed'
                    error_message = "Agent completed but start.sh was not generated"
                    self.logger.error(error_message)
            else:
                error_message = f"Claude Code exited with code {proc.returncode}"
                self.logger.error(error_message)

        except Exception as e:
            error_message = f"Error running Claude Code: {e}"
            self.logger.error(error_message, exc_info=True)
            logs.append(error_message)

            import traceback
            logs.append(traceback.format_exc())

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        return {
            'status': status,
            'logs': logs,
            'conversation': conversation if 'conversation' in dir() else [],
            'error': error_message,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'duration': duration,
            'project_info': project_info,
            'framework': self.framework_name,
            'model': self.model,
            'sandbox': True
        }

    async def _kill_container_claude(self, container_id: str) -> None:
        """Kill any lingering claude process inside the container.

        Killing the host-side `docker exec` client does not necessarily stop the
        process it spawned inside the container, which would otherwise keep
        running (and keep the container busy) with no API activity. The engine
        stops/removes the container afterwards, but we proactively reap the
        in-container claude process so results can still be copied out cleanly.
        """
        if not container_id:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", container_id,
                "pkill", "-9", "-f", "claude",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except Exception as e:
            self.logger.warning(f"Failed to kill in-container claude process: {e}")
