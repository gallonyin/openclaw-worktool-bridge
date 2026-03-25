import { useEffect, useState } from 'react';
import { QuestionCircleOutlined } from '@ant-design/icons';
import { Button, Card, Form, Input, Modal, Popconfirm, Popover, Select, Space, Switch, Table, Typography, message } from 'antd';
import { api } from '../api';
import type { Provider } from '../types';

const OPENAI_BASE_OPTIONS = [
  { label: 'OpenAI 官方', value: 'https://api.openai.com/v1/chat/completions' },
  { label: '硅基流动', value: 'https://api.siliconflow.cn/v1/chat/completions' },
  { label: '火山引擎', value: 'https://ark.cn-beijing.volces.com/api/v3/chat/completions' },
  { label: '自定义（OpenAI兼容接口）', value: '__custom__' }
];
const OPENCLAW_WEBHOOK_HINT = 'http://{你的外网ip:18799}/wechat/webhook?robotId={你的机器人id}';

export default function AIHubPage() {
  const [items, setItems] = useState<Provider[]>([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Provider | null>(null);
  const [providerType, setProviderType] = useState<'openai' | 'openclaw'>('openai');
  const [useCustomOpenaiUrl, setUseCustomOpenaiUrl] = useState(false);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  const normalizeProviderPayload = (rawValues: any, isEditing: boolean) => {
    const values = { ...rawValues };
    if (values.provider_type === 'openai') {
      if (values.base_url_preset && values.base_url_preset !== '__custom__') {
        values.base_url = values.base_url_preset;
      } else {
        values.base_url = values.base_url_openai;
      }
    } else {
      values.base_url = values.base_url_openclaw;
    }
    delete values.base_url_preset;
    delete values.base_url_openai;
    delete values.base_url_openclaw;
    values.auth_scheme = values.provider_type === 'openclaw' ? 'x-openclaw-token' : 'bearer';
    if (!values.api_token) {
      values.api_token = '';
    }
    if (isEditing && !values.api_token) {
      delete values.api_token;
    }
    return values;
  };

  const load = async () => {
    try {
      const ps = await api.listProviders();
      setItems(ps);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载AI回复引擎失败，请刷新页面后重试。');
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const submit = async () => {
    try {
      setSaving(true);
      const rawValues = await form.validateFields();
      const values = normalizeProviderPayload(rawValues, Boolean(editing));
      if (editing) {
        await api.updateProvider(editing.id, values);
        message.success('AI回复引擎更新成功');
      } else {
        await api.createProvider(values);
        message.success('AI回复引擎创建成功');
      }
      setOpen(false);
      load();
    } catch (e: any) {
      message.error(`${e?.response?.data?.detail || e?.message || '保存失败'}。请检查 Base URL 和 API Token 是否可用后重试。`);
    } finally {
      setSaving(false);
    }
  };

  const onTestProvider = async () => {
    try {
      const rawValues = await form.validateFields();
      const values = normalizeProviderPayload(rawValues, Boolean(editing));
      if (editing) {
        values.provider_id = editing.id;
      }
      setTesting(true);
      const res = await api.providerTest(values);
      const sec = Number(res?.elapsed_seconds || 0);
      message.success(`测试成功，响应耗时 ${sec.toFixed(1)}s`);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '测试失败');
    } finally {
      setTesting(false);
    }
  };

  const onDeleteProvider = async (row: Provider) => {
    try {
      await api.deleteProvider(row.id);
      message.success('AI回复引擎已删除');
      await load();
    } catch (e: any) {
      message.error(`${e?.response?.data?.detail || e?.message || '删除失败'}。若该引擎已被规则引用，请先删除或修改对应规则。`);
    }
  };

  const baseUrlHelp = (
    <Space direction="vertical" size={4}>
      <div>这是啥：AI 服务接口地址。</div>
      <div>为什么填：系统要通过它把用户问题发给模型。</div>
      <div>怎么填：优先使用下拉默认地址，只有自建服务才手动填写。</div>
      <div>示例：一般不用改，默认即可</div>
    </Space>
  );

  const tokenHelp = (
    <Space direction="vertical" size={4}>
      <div>这是啥：你的模型访问凭证。</div>
      <div>为什么填：部分平台需要，部分平台可留空。</div>
      <div>怎么填：去对应模型平台复制后粘贴；不需要可留空。</div>
      <div>示例：仅你自己的模型密钥，不会明文展示。</div>
    </Space>
  );

  const modelHelp = (
    <Space direction="vertical" size={4}>
      <div>这是啥：模型标识，部分平台也叫 model_id。</div>
      <div>为什么填：同一个平台有多个模型，不填可能调用到错误模型。</div>
      <div>怎么填：到模型平台控制台/API文档中查“模型名称”或“model_id”。</div>
      <div>示例：doubao-seed-2.0-lite、gpt-4o-mini、Qwen/Qwen3-8B</div>
    </Space>
  );

  return (
    <Card
      title={(
        <Space direction="vertical" size={0}>
          <span>AI回复引擎</span>
          <Typography.Text type="secondary">管理机器人回复时调用的模型服务</Typography.Text>
        </Space>
      )}
      extra={<Button type="primary" onClick={() => {
        setEditing(null);
        form.resetFields();
        setProviderType('openai');
        setUseCustomOpenaiUrl(false);
        form.setFieldsValue({
          enabled: true,
          provider_type: 'openai',
          base_url_preset: OPENAI_BASE_OPTIONS[0].value,
          base_url_openai: OPENAI_BASE_OPTIONS[0].value,
          base_url_openclaw: ''
        });
        setOpen(true);
      }}>新增 AI回复引擎</Button>}
    >
      <Table
        rowKey="id"
        dataSource={items}
        columns={[
          {
            title: '序号',
            width: 80,
            render: (_: unknown, __: Provider, index: number) => index + 1
          },
          { title: '名称', dataIndex: 'name' },
          { title: '类型', dataIndex: 'provider_type', width: 110 },
          { title: 'Base URL', dataIndex: 'base_url', ellipsis: true },
          { title: 'Token', dataIndex: 'api_token_masked' },
          {
            title: '状态',
            dataIndex: 'enabled',
            render: (v, row: Provider) => (row.is_system ? '系统默认' : v ? '启用' : '停用')
          },
          {
            title: '操作',
            render: (_, row: Provider) => (
              <Space>
                {row.can_manage === false ? (
                  <Typography.Text type="secondary">无法修改</Typography.Text>
                ) : (
                  <>
                    <Button size="small" onClick={() => {
                      setEditing(row);
                      const nextType = row.provider_type || 'openai';
                      setProviderType(nextType);
                      const preset = OPENAI_BASE_OPTIONS.find((x) => x.value === row.base_url);
                      const custom = nextType === 'openai' && !preset;
                      setUseCustomOpenaiUrl(custom);
                      form.setFieldsValue({
                        name: row.name,
                        base_url_openai: row.base_url,
                        base_url_openclaw: row.base_url,
                        base_url_preset: preset ? preset.value : '__custom__',
                        model: row.model,
                        provider_type: nextType,
                        extra_json: row.extra_json || '',
                        enabled: row.enabled
                      });
                      setOpen(true);
                    }}>编辑</Button>
                    <Popconfirm
                      title="确认删除该AI回复引擎？"
                      description={`名称：${row.name || '-'}`}
                      okText="删除"
                      cancelText="取消"
                      okButtonProps={{ danger: true }}
                      onConfirm={() => void onDeleteProvider(row)}
                    >
                      <Button size="small" danger>删除</Button>
                    </Popconfirm>
                  </>
                )}
              </Space>
            )
          }
        ]}
      />

      <Modal
        title={editing ? '编辑 AI回复引擎' : '新增 AI回复引擎'}
        open={open}
        onCancel={() => setOpen(false)}
        footer={[
          <Button
            key="test"
            onClick={() => void onTestProvider()}
            loading={testing}
            style={{ background: '#fff1f0', borderColor: '#ffccc7', color: '#cf1322' }}
          >
            测试接口
          </Button>,
          <Button key="cancel" onClick={() => setOpen(false)}>
            取消
          </Button>,
          <Button key="ok" type="primary" loading={saving} onClick={() => void submit()}>
            确定
          </Button>
        ]}
        destroyOnClose
        width={680}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="provider_type" label="引擎类型" rules={[{ required: true }]}>
            <Select
              onChange={(v: 'openai' | 'openclaw') => {
                setProviderType(v);
                if (v === 'openai') {
                  const current = form.getFieldValue('base_url_openai') || '';
                  const preset = OPENAI_BASE_OPTIONS.find((x) => x.value === current);
                  if (preset) {
                    setUseCustomOpenaiUrl(false);
                    form.setFieldsValue({ base_url_preset: preset.value });
                  } else if (current) {
                    setUseCustomOpenaiUrl(true);
                    form.setFieldsValue({ base_url_preset: '__custom__' });
                  } else {
                    setUseCustomOpenaiUrl(false);
                    form.setFieldsValue({ base_url_preset: OPENAI_BASE_OPTIONS[0].value, base_url_openai: OPENAI_BASE_OPTIONS[0].value });
                  }
                }
              }}
              options={[
                { label: 'openai(大模型)', value: 'openai' },
                { label: 'openclaw(小龙虾)', value: 'openclaw' }
              ]}
            />
          </Form.Item>
          {providerType === 'openai' ? (
            <>
              <Form.Item
                name="base_url_preset"
                label={(
                  <Space size={6}>
                    <span>Base URL</span>
                    <Popover content={baseUrlHelp} trigger="hover" placement="right">
                      <QuestionCircleOutlined style={{ color: '#8c8c8c' }} />
                    </Popover>
                  </Space>
                )}
                rules={[{ required: true }]}
              >
                <Select
                  options={OPENAI_BASE_OPTIONS}
                  onChange={(v: string) => {
                    if (v === '__custom__') {
                      setUseCustomOpenaiUrl(true);
                      if (!form.getFieldValue('base_url_openai')) {
                        form.setFieldsValue({ base_url_openai: 'https://' });
                      }
                      return;
                    }
                    setUseCustomOpenaiUrl(false);
                    form.setFieldsValue({ base_url_openai: v });
                  }}
                />
              </Form.Item>
              {useCustomOpenaiUrl ? (
                <Form.Item
                  name="base_url_openai"
                  label={(
                    <Space size={6}>
                      <span>手动 Base URL</span>
                      <Popover content={baseUrlHelp} trigger="hover" placement="right">
                        <QuestionCircleOutlined style={{ color: '#8c8c8c' }} />
                      </Popover>
                    </Space>
                  )}
                  rules={[{ required: true }]}
                >
                  <Input placeholder="https://your-endpoint/v1/chat/completions" />
                </Form.Item>
              ) : null}
            </>
          ) : (
            <Form.Item
              name="base_url_openclaw"
              label={(
                <Space size={6}>
                  <span>Base URL</span>
                  <Popover content={baseUrlHelp} trigger="hover" placement="right">
                    <QuestionCircleOutlined style={{ color: '#8c8c8c' }} />
                  </Popover>
                </Space>
              )}
              rules={[{ required: true }]}
            >
              <Input placeholder={OPENCLAW_WEBHOOK_HINT} />
            </Form.Item>
          )}
          <Form.Item
            name="api_token"
            label={(
              <Space size={6}>
                <span>API Token</span>
                <Popover content={tokenHelp} trigger="hover" placement="right">
                  <QuestionCircleOutlined style={{ color: '#8c8c8c' }} />
                </Popover>
              </Space>
            )}
          >
            <Input.Password placeholder={editing ? '留空表示不变更（也可不填）' : '可选，不需要可留空'} />
          </Form.Item>
          {providerType === 'openai' ? (
            <>
              <Form.Item
                name="model"
                label={(
                  <Space size={6}>
                    <span>Model</span>
                    <Popover content={modelHelp} trigger="hover" placement="right">
                      <QuestionCircleOutlined style={{ color: '#8c8c8c' }} />
                    </Popover>
                  </Space>
                )}
              >
                <Input />
              </Form.Item>
              <Form.Item
                name="extra_json"
                label="扩展 JSON"
                tooltip='可选，例如 {"request_headers":{"x-openclaw-agent-id":"xxx"},"push_secret":"xxx"}'
              >
                <Input.TextArea rows={5} />
              </Form.Item>
            </>
          ) : null}
          <Form.Item name="enabled" valuePropName="checked" label="启用">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
