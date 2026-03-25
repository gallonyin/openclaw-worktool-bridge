import { useEffect, useMemo, useState } from 'react';
import { Button, Card, Input, Popconfirm, Space, Table, Tag, Typography, message } from 'antd';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { api } from '../api';

interface BlacklistRow {
  key: string;
  ip: string;
}

function modeText(mode?: string) {
  const m = String(mode || '').trim();
  if (m === 'whitelist_only') return '当前模式：白名单优先（存在白名单）';
  if (m === 'blacklist_block') return '当前模式：黑名单拦截（白名单为空）';
  return '当前模式：默认全放行';
}

export default function IpBlacklistPage() {
  const [ip, setIp] = useState('');
  const [mode, setMode] = useState('');
  const [rows, setRows] = useState<BlacklistRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const res = await api.adminIpAclBlacklistQuery();
      const list = Array.isArray(res?.blacklist) ? res.blacklist : [];
      setMode(String(res?.mode || ''));
      setRows(
        list.map((x: any) => {
          const value = String(x || '').trim();
          return { key: value, ip: value };
        }).filter((x: BlacklistRow) => Boolean(x.ip))
      );
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '黑名单加载失败');
      setMode('');
      setRows([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const columns = useMemo(
    () => [
      { title: 'IP地址', dataIndex: 'ip' },
      {
        title: '操作',
        width: 120,
        render: (_: any, row: BlacklistRow) => (
          <Popconfirm
            title={`确认删除黑名单IP ${row.ip}？`}
            okText="删除"
            cancelText="取消"
            onConfirm={async () => {
              try {
                await api.adminIpAclBlacklistDelete(row.ip);
                message.success('删除成功');
                await load();
              } catch (e: any) {
                message.error(e?.response?.data?.detail || '删除失败');
              }
            }}
          >
            <Button size="small" danger>删除</Button>
          </Popconfirm>
        ),
      },
    ],
    []
  );

  return (
    <Card
      title="黑名单管理"
      extra={(
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
        </Space>
      )}
    >
      <Space style={{ marginBottom: 12 }}>
        <Input
          style={{ width: 260 }}
          placeholder="输入IP，例如 1.2.3.4"
          value={ip}
          onChange={(e) => setIp(e.target.value)}
          onPressEnter={async () => {
            const target = ip.trim();
            if (!target) {
              message.warning('请输入IP');
              return;
            }
            setSaving(true);
            try {
              await api.adminIpAclBlacklistAdd(target);
              message.success('添加成功');
              setIp('');
              await load();
            } catch (e: any) {
              message.error(e?.response?.data?.detail || '添加失败');
            } finally {
              setSaving(false);
            }
          }}
        />
        <Button
          type="primary"
          icon={<PlusOutlined />}
          loading={saving}
          onClick={async () => {
            const target = ip.trim();
            if (!target) {
              message.warning('请输入IP');
              return;
            }
            setSaving(true);
            try {
              await api.adminIpAclBlacklistAdd(target);
              message.success('添加成功');
              setIp('');
              await load();
            } catch (e: any) {
              message.error(e?.response?.data?.detail || '添加失败');
            } finally {
              setSaving(false);
            }
          }}
        >
          新增黑名单IP
        </Button>
        <Tag color="orange">{modeText(mode)}</Tag>
      </Space>

      <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 10 }}>
        仅管理黑名单。黑名单生效规则以 WorkTool ACL 模式为准。
      </Typography.Text>

      <Table
        rowKey="key"
        loading={loading}
        dataSource={rows}
        pagination={false}
        columns={columns}
        locale={{ emptyText: '暂无黑名单IP' }}
      />
    </Card>
  );
}
