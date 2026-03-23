import { useEffect, useMemo, useState } from 'react';
import { Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message } from 'antd';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { api } from '../api';

type AdminStatus = 'all' | 'draft' | 'published' | 'offline';

interface InboxAdminRow {
  id: number;
  category: 'system' | 'ops';
  level: 'info' | 'warning' | 'error';
  title: string;
  content: string;
  recipient_scope?: { type?: 'all' | 'admins' | 'phones'; phones?: string[] };
  status: 'draft' | 'published' | 'offline';
  publish_at?: string | null;
  expire_at?: string | null;
  created_at: string;
  updated_at: string;
}

const levelOptions = [
  { label: '普通', value: 'info' },
  { label: '提醒', value: 'warning' },
  { label: '严重', value: 'error' },
];

function toScopePayload(values: any) {
  const tp = String(values.recipient_type || 'all');
  if (tp !== 'phones') return { type: tp };
  const phones = String(values.recipient_phones || '')
    .split(/[\n,，;；\s]+/)
    .map((x) => x.trim())
    .filter(Boolean);
  return { type: 'phones', phones };
}

function fromScope(scope?: { type?: 'all' | 'admins' | 'phones'; phones?: string[] }) {
  const tp = String(scope?.type || 'all');
  if (tp !== 'phones') {
    return { recipient_type: tp, recipient_phones: '' };
  }
  return {
    recipient_type: 'phones',
    recipient_phones: (scope?.phones || []).join('\n'),
  };
}

export default function InboxAdminPage() {
  const [status, setStatus] = useState<AdminStatus>('all');
  const [rows, setRows] = useState<InboxAdminRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);

  const [open, setOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  const loadRows = async (nextPage = page, nextPageSize = pageSize) => {
    setLoading(true);
    try {
      const res = await api.adminInboxMessages({ page: nextPage, page_size: nextPageSize, status });
      setRows((res?.items || []) as InboxAdminRow[]);
      setTotal(Number(res?.total || 0));
      setPage(Number(res?.page || nextPage));
      setPageSize(Number(res?.page_size || nextPageSize));
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '拉取站内信配置失败');
      setRows([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadRows(1, pageSize);
  }, [status]);

  const openCreate = () => {
    setEditingId(null);
    form.resetFields();
    form.setFieldsValue({
      category: 'ops',
      level: 'info',
      recipient_type: 'all',
      publish_now: false,
    });
    setOpen(true);
  };

  const openEdit = (row: InboxAdminRow) => {
    setEditingId(row.id);
    form.resetFields();
    form.setFieldsValue({
      category: row.category,
      level: row.level,
      title: row.title,
      content: row.content,
      publish_at: row.publish_at || '',
      expire_at: row.expire_at || '',
      publish_now: row.status === 'published',
      ...fromScope(row.recipient_scope),
    });
    setOpen(true);
  };

  const submit = async () => {
    const values = await form.validateFields();
    const payload = {
      category: values.category,
      level: values.level,
      title: String(values.title || '').trim(),
      content: String(values.content || '').trim(),
      recipient_scope: toScopePayload(values),
      publish_at: String(values.publish_at || '').trim() || null,
      expire_at: String(values.expire_at || '').trim() || null,
      publish_now: Boolean(values.publish_now),
    };
    setSaving(true);
    try {
      if (editingId) {
        await api.adminUpdateInboxMessage(editingId, payload);
      } else {
        await api.adminCreateInboxMessage(payload);
      }
      message.success('保存成功');
      setOpen(false);
      await loadRows(1, pageSize);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const levelColor = (level: 'info' | 'warning' | 'error') => {
    if (level === 'warning') return 'orange';
    if (level === 'error') return 'red';
    return 'blue';
  };

  const scopeText = (row: InboxAdminRow) => {
    const tp = String(row?.recipient_scope?.type || 'all');
    if (tp === 'admins') return '仅管理员';
    if (tp === 'phones') return `指定手机号(${(row?.recipient_scope?.phones || []).length})`;
    return '全部用户';
  };

  const columns = useMemo(
    () => [
      { title: 'ID', dataIndex: 'id', width: 80 },
      { title: '标题', dataIndex: 'title', ellipsis: true },
      {
        title: '级别',
        dataIndex: 'level',
        width: 90,
        render: (v: 'info' | 'warning' | 'error') => <Tag color={levelColor(v)}>{v}</Tag>,
      },
      {
        title: '范围',
        width: 150,
        render: (_: any, row: InboxAdminRow) => scopeText(row),
      },
      {
        title: '状态',
        dataIndex: 'status',
        width: 110,
        render: (v: 'draft' | 'published' | 'offline') => <Tag>{v}</Tag>,
      },
      { title: '发布时间', dataIndex: 'publish_at', width: 180, render: (v: string | null | undefined) => v || '-' },
      { title: '过期时间', dataIndex: 'expire_at', width: 180, render: (v: string | null | undefined) => v || '-' },
      {
        title: '操作',
        width: 280,
        render: (_: any, row: InboxAdminRow) => (
          <Space>
            <Button size="small" onClick={() => openEdit(row)}>编辑</Button>
            <Button
              size="small"
              type="primary"
              ghost
              disabled={row.status === 'published'}
              onClick={async () => {
                try {
                  await api.adminPublishInboxMessage(row.id);
                  message.success('已发布');
                  await loadRows(page, pageSize);
                } catch (e: any) {
                  message.error(e?.response?.data?.detail || '发布失败');
                }
              }}
            >
              发布
            </Button>
            <Button
              size="small"
              disabled={row.status !== 'published'}
              onClick={async () => {
                try {
                  await api.adminOfflineInboxMessage(row.id);
                  message.success('已下线');
                  await loadRows(page, pageSize);
                } catch (e: any) {
                  message.error(e?.response?.data?.detail || '下线失败');
                }
              }}
            >
              下线
            </Button>
            <Popconfirm
              title="确认删除该站内信？"
              okText="删除"
              cancelText="取消"
              onConfirm={async () => {
                try {
                  await api.adminDeleteInboxMessage(row.id);
                  message.success('删除成功');
                  await loadRows(page, pageSize);
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
    [page, pageSize]
  );

  return (
    <Card
      title="站内信配置"
      extra={(
        <Space>
          <Button icon={<PlusOutlined />} type="primary" onClick={openCreate}>新增</Button>
          <Button icon={<ReloadOutlined />} onClick={() => loadRows(page, pageSize)}>刷新</Button>
        </Space>
      )}
    >
      <Space style={{ marginBottom: 12 }}>
        <Select
          style={{ width: 160 }}
          value={status}
          onChange={(v) => setStatus(v as AdminStatus)}
          options={[
            { label: '全部状态', value: 'all' },
            { label: '草稿', value: 'draft' },
            { label: '已发布', value: 'published' },
            { label: '已下线', value: 'offline' },
          ]}
        />
        <Typography.Text type="secondary">支持新增、删除、修改内容与接收对象范围。</Typography.Text>
      </Space>

      <Table
        rowKey="id"
        loading={loading}
        columns={columns as any}
        dataSource={rows}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          onChange: (p, s) => {
            void loadRows(p, s);
          },
        }}
        scroll={{ x: 1500 }}
      />

      <Modal
        title={editingId ? '编辑站内信' : '新增站内信'}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={submit}
        confirmLoading={saving}
        width={760}
      >
        <Form form={form} layout="vertical">
          <Space style={{ width: '100%' }}>
            <Form.Item name="category" label="类别" rules={[{ required: true }]}>
              <Select style={{ width: 140 }} options={[{ label: '运营', value: 'ops' }, { label: '系统', value: 'system' }]} />
            </Form.Item>
            <Form.Item name="level" label="级别" rules={[{ required: true }]}>
              <Select style={{ width: 140 }} options={levelOptions} />
            </Form.Item>
            <Form.Item name="publish_now" label="发布状态" rules={[{ required: true }]}>
              <Select
                style={{ width: 180 }}
                options={[
                  { label: '保存为草稿', value: false },
                  { label: '保存并立即发布', value: true },
                ]}
              />
            </Form.Item>
          </Space>
          <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入标题' }]}>
            <Input maxLength={255} />
          </Form.Item>
          <Form.Item name="content" label="内容（纯文本）" rules={[{ required: true, message: '请输入内容' }]}>
            <Input.TextArea rows={6} />
          </Form.Item>
          <Form.Item name="recipient_type" label="接收范围" rules={[{ required: true }]}> 
            <Select
              options={[
                { label: '全部用户', value: 'all' },
                { label: '仅管理员', value: 'admins' },
                { label: '指定手机号', value: 'phones' },
              ]}
            />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(p, n) => p.recipient_type !== n.recipient_type}>
            {({ getFieldValue }) => {
              if (getFieldValue('recipient_type') !== 'phones') return null;
              return (
                <Form.Item
                  name="recipient_phones"
                  label="指定手机号（换行/逗号分隔）"
                  rules={[{ required: true, message: '请输入至少一个手机号' }]}
                >
                  <Input.TextArea rows={4} />
                </Form.Item>
              );
            }}
          </Form.Item>
          <Space style={{ width: '100%' }}>
            <Form.Item name="publish_at" label="发布时间（可空）">
              <Input placeholder="YYYY-MM-DD HH:MM:SS" style={{ width: 260 }} />
            </Form.Item>
            <Form.Item name="expire_at" label="过期时间（可空）">
              <Input placeholder="YYYY-MM-DD HH:MM:SS" style={{ width: 260 }} />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </Card>
  );
}
