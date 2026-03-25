import { useEffect, useMemo, useState } from 'react';
import { Button, Card, Form, Input, Modal, Popconfirm, Space, Switch, Table, Tag, Typography, message } from 'antd';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { api } from '../api';

interface AuthRow {
  corpId: string;
  corpName?: string;
  agentId?: string;
  isEnabled?: boolean;
  expireTime?: string;
  remark?: string;
}

function pickRows(res: any): AuthRow[] {
  const data = res?.data;
  if (Array.isArray(data)) return data as AuthRow[];
  if (Array.isArray(data?.list)) return data.list as AuthRow[];
  if (Array.isArray(data?.records)) return data.records as AuthRow[];
  if (Array.isArray(res?.list)) return res.list as AuthRow[];
  return [];
}

export default function EnterpriseAuthorizationPage() {
  const [corpId, setCorpId] = useState('');
  const [corpName, setCorpName] = useState('');
  const [loading, setLoading] = useState(false);
  const [rows, setRows] = useState<AuthRow[]>([]);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<AuthRow | null>(null);
  const [form] = Form.useForm();

  const load = async () => {
    setLoading(true);
    try {
      const res = await api.adminWeworkAuthorizationList({
        corp_id: corpId.trim() || undefined,
        corp_name: corpName.trim() || undefined,
      });
      setRows(pickRows(res));
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载企业授权列表失败');
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
      { title: 'CorpId', dataIndex: 'corpId', width: 220, ellipsis: true },
      { title: '企业名称', dataIndex: 'corpName', width: 160, render: (v: string | undefined) => v || '-' },
      { title: 'AgentId', dataIndex: 'agentId', width: 120, render: (v: string | undefined) => v || '-' },
      {
        title: '状态',
        dataIndex: 'isEnabled',
        width: 100,
        render: (v: boolean | undefined) => <Tag color={v ? 'green' : 'default'}>{v ? '启用' : '停用'}</Tag>,
      },
      { title: '到期时间', dataIndex: 'expireTime', width: 180, render: (v: string | undefined) => v || '-' },
      { title: '备注', dataIndex: 'remark', ellipsis: true, render: (v: string | undefined) => v || '-' },
      {
        title: '操作',
        width: 180,
        render: (_: any, row: AuthRow) => (
          <Space>
            <Button
              size="small"
              onClick={() => {
                setEditing(row);
                form.setFieldsValue({
                  corpId: row.corpId,
                  corpName: row.corpName || '',
                  agentId: row.agentId || '',
                  isEnabled: row.isEnabled !== false,
                  expireTime: row.expireTime || '',
                  remark: row.remark || '',
                });
                setOpen(true);
              }}
            >
              编辑
            </Button>
            <Popconfirm
              title={`确认删除企业授权 ${row.corpId}？`}
              okText="删除"
              cancelText="取消"
              onConfirm={async () => {
                try {
                  await api.adminWeworkAuthorizationDelete(row.corpId);
                  message.success('删除成功');
                  await load();
                } catch (e: any) {
                  message.error(e?.response?.data?.detail || '删除失败');
                }
              }}
            >
              <Button size="small" danger>删除</Button>
            </Popconfirm>
          </Space>
        ),
      },
    ],
    []
  );

  return (
    <Card
      title="企业定制开通"
      extra={(
        <Space>
          <Button
            icon={<PlusOutlined />}
            type="primary"
            onClick={() => {
              setEditing(null);
              form.resetFields();
              form.setFieldsValue({ isEnabled: true });
              setOpen(true);
            }}
          >
            新增企业授权
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
        </Space>
      )}
    >
      <Space style={{ marginBottom: 12 }}>
        <Input
          style={{ width: 260 }}
          value={corpId}
          onChange={(e) => setCorpId(e.target.value)}
          placeholder="按 corpId 查询"
          onPressEnter={() => void load()}
        />
        <Input
          style={{ width: 260 }}
          value={corpName}
          onChange={(e) => setCorpName(e.target.value)}
          placeholder="按 corpName 查询"
          onPressEnter={() => void load()}
        />
        <Button onClick={() => void load()}>查询</Button>
      </Space>
      <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 10 }}>
        管理企业授权（新增/修改/删除），仅管理员可见。
      </Typography.Text>

      <Table
        rowKey={(r) => r.corpId}
        dataSource={rows}
        loading={loading}
        columns={columns}
        pagination={false}
        scroll={{ x: 1100 }}
        locale={{ emptyText: '暂无企业授权' }}
      />

      <Modal
        title={editing ? '编辑企业授权' : '新增企业授权'}
        open={open}
        onCancel={() => setOpen(false)}
        confirmLoading={saving}
        onOk={async () => {
          const values = await form.validateFields();
          setSaving(true);
          try {
            await api.adminWeworkAuthorizationSave({
              corpId: String(values.corpId || '').trim(),
              corpName: String(values.corpName || '').trim() || undefined,
              agentId: String(values.agentId || '').trim() || undefined,
              isEnabled: Boolean(values.isEnabled),
              expireTime: String(values.expireTime || '').trim() || undefined,
              remark: String(values.remark || '').trim() || undefined,
            });
            message.success('保存成功');
            setOpen(false);
            await load();
          } catch (e: any) {
            message.error(e?.response?.data?.detail || '保存失败');
          } finally {
            setSaving(false);
          }
        }}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="corpId" label="CorpId" rules={[{ required: true, message: '请输入 corpId' }]}>
            <Input disabled={Boolean(editing)} placeholder="ww1234567890abcdef" />
          </Form.Item>
          <Form.Item name="corpName" label="企业名称">
            <Input placeholder="测试企业A" />
          </Form.Item>
          <Form.Item name="agentId" label="AgentId">
            <Input placeholder="1000002" />
          </Form.Item>
          <Form.Item name="isEnabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="expireTime" label="到期时间">
            <Input placeholder="2027-03-25T23:59:59" />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={3} placeholder="首年授权" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
