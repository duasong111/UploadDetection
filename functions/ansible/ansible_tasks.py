"""
Ansible 任务调度模块
简单封装 subprocess 调用 ansible-playbook
"""
import subprocess
import json
import os
from pathlib import Path
from typing import List, Dict, Optional

# Ansible 项目目录
ANSIBLE_DIR = Path(__file__).parent.parent / "ansible"
ANSIBLE_PLAYBOOKS_DIR = ANSIBLE_DIR / "playbooks"
ANSIBLE_CFG = ANSIBLE_DIR / "ansible.cfg"


class AnsibleTaskRunner:
    """Ansible 任务执行器"""

    def __init__(self):
        self.ansible_dir = ANSIBLE_DIR
        self.cfg_file = ANSIBLE_CFG

    def _run(self, playbook: str, hosts: List[str] = None, **extra_vars) -> Dict:
        """
        执行 Ansible Playbook

        Args:
            playbook: playbook 文件名
            hosts: 目标主机 IP 列表 (可选)
            **extra_vars: 传递给 playbook 的变量

        Returns:
            {'success': bool, 'output': str, 'returncode': int}
        """
        playbook_path = ANSIBLE_PLAYBOOKS_DIR / playbook

        if not playbook_path.exists():
            return {
                'success': False,
                'output': f'Playbook 不存在: {playbook_path}',
                'returncode': -1
            }

        # 构建 ansible-playbook 命令
        cmd = [
            'ansible-playbook',
            str(playbook_path),
            '-i', str(self.ansible_dir / 'inventory.ini'),
            '-c', 'paramiko',  # 使用 paramiko 连接方式
        ]

        # 添加变量
        if extra_vars:
            for key, value in extra_vars.items():
                if value is not None:
                    cmd.extend([ '-e', f'{key}="{value}"' ])

        # 添加限制主机 (可选)
        if hosts:
            cmd.extend([ '-l', ','.join(hosts) ])

        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.ansible_dir),
                capture_output=True,
                text=True,
                timeout=300  # 5分钟超时
            )

            return {
                'success': result.returncode == 0,
                'output': result.stdout + (result.stderr if result.stderr else ''),
                'returncode': result.returncode,
                'cmd': ' '.join(cmd)
            }

        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'output': '执行超时 (5分钟)',
                'returncode': -1,
                'cmd': ' '.join(cmd)
            }
        except Exception as e:
            return {
                'success': False,
                'output': f'执行失败: {str(e)}',
                'returncode': -1,
                'cmd': ' '.join(cmd)
            }

    def replace_file(
        self,
        hosts: List[str],
        target_path: str,
        file_content: str,
        file_mode: str = '0644'
    ) -> Dict:
        """
        替换远程文件

        Args:
            hosts: 目标主机 IP 列表
            target_path: 远程文件路径
            file_content: 文件内容
            file_mode: 文件权限 (如 '0755')

        Returns:
            执行结果
        """
        return self._run(
            'replace.yml',
            hosts,
            target_path=target_path,
            file_content=file_content,
            file_mode=file_mode
        )

    def manage_service(
        self,
        hosts: List[str],
        service_name: str,
        service_path: str,
        service_content: str,
        service_state: str = 'started',
        service_enabled: bool = True,
        service_action: str = 'start'
    ) -> Dict:
        """
        管理 Systemd 服务

        Args:
            hosts: 目标主机
            service_name: 服务名称 (不含 .service)
            service_path: 服务文件路径
            service_content: 服务文件内容
            service_state: 服务状态 (started/stopped/restarted)
            service_enabled: 是否开机启动
            service_action: 操作描述

        Returns:
            执行结果
        """
        return self._run(
            'systemd_service.yml',
            hosts,
            service_name=service_name,
            service_path=service_path,
            service_content=service_content,
            service_state=service_state,
            service_enabled=str(service_enabled).lower(),
            service_action=service_action
        )

    def execute_command(self, hosts: List[str], cmd: str) -> Dict:
        """
        执行远程命令

        Args:
            hosts: 目标主机
            cmd: 要执行的命令

        Returns:
            执行结果
        """
        return self._run('execute_command.yml', hosts, cmd=cmd)

    def test_connection(self, hosts: List[str] = None) -> Dict:
        """
        测试连接

        Args:
            hosts: 目标主机 (不传则测试所有)

        Returns:
            测试结果
        """
        return self._run('execute_command.yml', hosts, cmd='echo "OK"')


# 全局实例
ansible_runner = AnsibleTaskRunner()
