import { useEffect, useState } from 'react';
import { Button, Card, Form, Input, Modal, Select, Space, Switch, Table, message } from 'antd';
import { api } from '../api';
import type { Provider } from '../types';

const OPENAI_BASE_OPTIONS = [
  { label: 'OpenAI 官方', value: 'https://api.openai.com/v1/chat/completions' },
  { label: '硅基流动', value: 'https://api.siliconflow.cn/v1/chat/completions' },
  { label: '火山引擎', value: 'https://ark.cn-beijing.volces.com/api/v3/chat/completions' },
  { label: '自定义（OpenAI兼容接口）', value: '__custom__' }
];

export default function AIHubPage() {
  const [items, setItems] = useState<Provider[]>([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Provider | null>(null);
  const [providerType, setProviderType] = useState<'openai' | 'openclaw'>('openai');
  const [useCustomOpenaiUrl, setUseCustomOpenaiUrl] = useState(false);
  const [form] = Form.useForm();

  const load = async () => {
    const ps = await api.listProviders();
    setItems(ps);
  };

  useEffect(() => {
    void load();
  }, []);

  const submit = async () => {
    const values = await form.validateFields();
    if (values.provider_type === 'openai' && values.base_url_preset && values.base_url_preset !== '__custom__') {
      values.base_url = values.base_url_preset;
    }
    delete values.base_url_preset;
    values.auth_scheme = values.provider_type === 'openclaw' ? 'x-openclaw-token' : 'bearer';
    if (editing) {
      if (!values.api_token) {
        delete values.api_token;
      }
      await api.updateProvider(editing.id, values);
      message.success('更新成功');
    } else {
      await api.createProvider(values);
      message.success('创建成功');
    }
    setOpen(false);
    load();
  };

  return (
    <Card
      title="API 仓库"
      extra={<Button type="primary" onClick={() => {
        setEditing(null);
        form.resetFields();
        setProviderType('openai');
        setUseCustomOpenaiUrl(false);
        form.setFieldsValue({ enabled: true, provider_type: 'openai', base_url_preset: OPENAI_BASE_OPTIONS[0].value, base_url: OPENAI_BASE_OPTIONS[0].value });
        setOpen(true);
      }}>新增 Provider</Button>}
    >
      <Table
        rowKey="id"
        dataSource={items}
        columns={[
          { title: 'ID', dataIndex: 'id', width: 80 },
          { title: '名称', dataIndex: 'name' },
          { title: '类型', dataIndex: 'provider_type', width: 110 },
          { title: 'Base URL', dataIndex: 'base_url', ellipsis: true },
          { title: 'Token', dataIndex: 'api_token_masked' },
          { title: '状态', dataIndex: 'enabled', render: (v) => (v ? '启用' : '停用') },
          {
            title: '操作',
            render: (_, row: Provider) => (
              <Space>
                <Button size="small" onClick={() => {
                  setEditing(row);
                  const nextType = row.provider_type || 'openai';
                  setProviderType(nextType);
                  const preset = OPENAI_BASE_OPTIONS.find((x) => x.value === row.base_url);
                  const custom = nextType === 'openai' && !preset;
                  setUseCustomOpenaiUrl(custom);
                  form.setFieldsValue({
                    name: row.name,
                    base_url: row.base_url,
                    base_url_preset: preset ? preset.value : '__custom__',
                    model: row.model,
                    provider_type: nextType,
                    extra_json: row.extra_json || '',
                    enabled: row.enabled
                  });
                  setOpen(true);
                }}>编辑</Button>
              </Space>
            )
          }
        ]}
      />

      <Modal
        title={editing ? '编辑 Provider' : '新增 Provider'}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={submit}
        destroyOnClose
        width={680}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="provider_type" label="Provider 类型" rules={[{ required: true }]}>
            <Select
              onChange={(v: 'openai' | 'openclaw') => {
                setProviderType(v);
                if (v === 'openai') {
                  const current = form.getFieldValue('base_url') || '';
                  const preset = OPENAI_BASE_OPTIONS.find((x) => x.value === current);
                  if (preset) {
                    setUseCustomOpenaiUrl(false);
                    form.setFieldsValue({ base_url_preset: preset.value });
                  } else if (current) {
                    setUseCustomOpenaiUrl(true);
                    form.setFieldsValue({ base_url_preset: '__custom__' });
                  } else {
                    setUseCustomOpenaiUrl(false);
                    form.setFieldsValue({ base_url_preset: OPENAI_BASE_OPTIONS[0].value, base_url: OPENAI_BASE_OPTIONS[0].value });
                  }
                }
              }}
              options={[
                { label: 'openai', value: 'openai' },
                { label: 'openclaw', value: 'openclaw' }
              ]}
            />
          </Form.Item>
          {providerType === 'openai' ? (
            <>
              <Form.Item name="base_url_preset" label="Base URL" rules={[{ required: true }]}>
                <Select
                  options={OPENAI_BASE_OPTIONS}
                  onChange={(v: string) => {
                    if (v === '__custom__') {
                      setUseCustomOpenaiUrl(true);
                      if (!form.getFieldValue('base_url')) {
                        form.setFieldsValue({ base_url: 'https://' });
                      }
                      return;
                    }
                    setUseCustomOpenaiUrl(false);
                    form.setFieldsValue({ base_url: v });
                  }}
                />
              </Form.Item>
              {useCustomOpenaiUrl ? (
                <Form.Item name="base_url" label="手动 Base URL" rules={[{ required: true }]}>
                  <Input placeholder="https://your-endpoint/v1/chat/completions" />
                </Form.Item>
              ) : null}
            </>
          ) : (
            <Form.Item name="base_url" label="Base URL" rules={[{ required: true }]}>
              <Input />
            </Form.Item>
          )}
          <Form.Item name="api_token" label="API Token" rules={editing ? [] : [{ required: true }]}>
            <Input.Password placeholder={editing ? '留空表示不变更' : ''} />
          </Form.Item>
          <Form.Item name="model" label="Model">
            <Input />
          </Form.Item>
          <Form.Item
            name="extra_json"
            label="扩展 JSON"
            tooltip='可选，例如 {"request_headers":{"x-openclaw-agent-id":"xxx"},"push_secret":"xxx"}'
          >
            <Input.TextArea rows={5} />
          </Form.Item>
          <Form.Item name="enabled" valuePropName="checked" label="启用">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
