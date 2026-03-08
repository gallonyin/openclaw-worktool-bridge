import { useEffect, useMemo, useState } from 'react';
import { ArrowDownOutlined, ArrowUpOutlined } from '@ant-design/icons';
import { Button, Card, Collapse, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Switch, Table, Tabs, Tag, Typography, message } from 'antd';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { Provider, Robot, Rule } from '../types';
import { SELECTED_ROBOT_STORAGE_KEY } from '../constants';

interface CallbackBaseSuggestions {
  current_request_base?: string;
  public_base?: string;
  intranet_bases?: string[];
  suggested_base?: string;
}

export default function RobotPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<Robot[]>([]);
  const [selectedRobotId, setSelectedRobotId] = useState<string | undefined>(() => {
    try {
      return localStorage.getItem(SELECTED_ROBOT_STORAGE_KEY) || undefined;
    } catch {
      return undefined;
    }
  });
  const [providers, setProviders] = useState<Provider[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [ruleScene, setRuleScene] = useState<'group' | 'private'>('group');

  const [robotOpen, setRobotOpen] = useState(false);
  const [editingRobot, setEditingRobot] = useState<Robot | null>(null);
  const [robotForm] = Form.useForm();

  const [ruleOpen, setRuleOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<Rule | null>(null);
  const [ruleForm] = Form.useForm();
  const [ruleSwitchLoading, setRuleSwitchLoading] = useState<number[]>([]);

  const [worktoolForm] = Form.useForm();
  const [savingWorktool, setSavingWorktool] = useState(false);
  const [callbackExampleUrl, setCallbackExampleUrl] = useState('');
  const [callbackBaseSuggestions, setCallbackBaseSuggestions] = useState<CallbackBaseSuggestions>({});

  const loadRobots = async () => {
    const res = await api.listRobots();
    setItems(res);
    if (res.length === 0) {
      setSelectedRobotId(undefined);
      return;
    }
    const current = selectedRobotId;
    const exists = current && res.some((x: Robot) => x.robot_id === current);
    if (!exists) {
      setSelectedRobotId(res[0].robot_id);
    }
  };

  const loadRulesAndProviders = async (robotId?: string) => {
    if (!robotId) return;
    const [ruleRes, providerRes] = await Promise.all([api.listRules(robotId), api.listProviders(robotId)]);
    setRules(ruleRes);
    setProviders(providerRes);
  };

  const loadWorktoolSettings = async () => {
    const res = await api.getWorktoolSettings();
    worktoolForm.setFieldsValue({
      worktool_api_base: res.worktool_api_base || '',
      callback_public_base_url: res.callback_public_base_url || '',
      auto_bind_message_callback_on_create: res.auto_bind_message_callback_on_create !== false
    });
    setCallbackExampleUrl(res.callback_example_url || '');
  };

  const loadCallbackBaseSuggestions = async () => {
    try {
      const res = await api.getCallbackBaseSuggestions();
      setCallbackBaseSuggestions(res || {});
    } catch {
      setCallbackBaseSuggestions({});
    }
  };

  useEffect(() => {
    void loadRobots();
    void loadWorktoolSettings();
    void loadCallbackBaseSuggestions();
  }, []);

  useEffect(() => {
    void loadRulesAndProviders(selectedRobotId);
  }, [selectedRobotId]);

  useEffect(() => {
    try {
      if (selectedRobotId) {
        localStorage.setItem(SELECTED_ROBOT_STORAGE_KEY, selectedRobotId);
      }
    } catch {
      // ignore storage errors
    }
  }, [selectedRobotId]);

  const providerOptions = useMemo(
    () => providers.map((p) => ({ label: `${p.name} (${p.id})`, value: p.id })),
    [providers]
  );
  const robotOptions = useMemo(
    () => items.map((r) => ({ label: `${r.name} (${r.robot_id})`, value: r.robot_id })),
    [items]
  );

  const sceneRules = useMemo(
    () => [...rules].filter((r) => r.scene === ruleScene).sort((a, b) => a.priority - b.priority || a.id - b.id),
    [rules, ruleScene]
  );

  const onMoveRule = async (rule: Rule, direction: -1 | 1) => {
    if (!selectedRobotId) return;
    const list = [...sceneRules];
    const index = list.findIndex((r) => r.id === rule.id);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= list.length) return;
    [list[index], list[target]] = [list[target], list[index]];
    await api.reorderRules(selectedRobotId, ruleScene, list.map((r) => r.id));
    message.success('规则排序已更新');
    await loadRulesAndProviders(selectedRobotId);
  };

  const onCreateRobot = () => {
    setEditingRobot(null);
    robotForm.resetFields();
    robotForm.setFieldsValue({
      name: '机器人',
      private_chat_enabled: true,
      group_chat_enabled: true,
      group_reply_only_when_mentioned: false
    });
    setRobotOpen(true);
  };

  const onEditRobot = (row: Robot) => {
    setEditingRobot(row);
    robotForm.setFieldsValue(row);
    setRobotOpen(true);
  };

  const onDeleteRobot = async (row: Robot) => {
    await api.deleteRobot(row.robot_id);
    message.success('机器人已删除');
    if (selectedRobotId === row.robot_id) {
      setSelectedRobotId(undefined);
      setRules([]);
    }
    await loadRobots();
  };

  const submitRobot = async () => {
    const values = await robotForm.validateFields();
    try {
      if (editingRobot) {
        await api.updateRobot(editingRobot.robot_id, values);
        message.success('机器人更新成功');
      } else {
        values.name = (values.name || '').trim() || '机器人';
        const res = await api.createRobot(values);
        if (res?.auto_bind_message_callback && res?.callback_url) {
          message.success(`机器人创建成功，已自动绑定消息回调：${res.callback_url}`);
        } else {
          message.success('机器人创建成功');
        }
      }
      setRobotOpen(false);
      await loadRobots();
    } catch (e: any) {
      Modal.error({
        title: editingRobot ? '更新失败' : '创建失败',
        content: e?.response?.data?.detail || e?.message || '未知错误',
      });
    }
  };

  const submitWorktoolSettings = async () => {
    const values = await worktoolForm.validateFields();
    const callbackPublicBaseUrl = (values.callback_public_base_url || '').trim();
    if (values.auto_bind_message_callback_on_create && !callbackPublicBaseUrl) {
      message.warning('开启自动绑定前，请先填写“回调公网基础地址”');
      return;
    }
    setSavingWorktool(true);
    try {
      const res = await api.updateWorktoolSettings({
        worktool_api_base: values.worktool_api_base,
        callback_public_base_url: callbackPublicBaseUrl,
        auto_bind_message_callback_on_create: !!values.auto_bind_message_callback_on_create
      });
      worktoolForm.setFieldsValue({
        worktool_api_base: res.worktool_api_base || '',
        callback_public_base_url: res.callback_public_base_url || '',
        auto_bind_message_callback_on_create: res.auto_bind_message_callback_on_create !== false
      });
      setCallbackExampleUrl(res.callback_example_url || '');
      message.success('平台设置已更新');
    } finally {
      setSavingWorktool(false);
    }
  };

  const copyWorktoolBase = async () => {
    const value = (worktoolForm.getFieldValue('worktool_api_base') || '').trim();
    if (!value) {
      message.warning('后台地址为空，无法复制');
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
      message.success('后台地址已复制');
    } catch {
      message.error('复制失败，请手动复制');
    }
  };

  const onCreateRule = () => {
    if (!selectedRobotId) {
      message.warning('请先选择机器人');
      return;
    }
    setEditingRule(null);
    ruleForm.resetFields();
    ruleForm.setFieldsValue({
      robot_id: selectedRobotId,
      scene: ruleScene,
      enabled: true,
      priority: 100
    });
    setRuleOpen(true);
  };

  const onEditRule = (row: Rule) => {
    setEditingRule(row);
    ruleForm.setFieldsValue(row);
    setRuleOpen(true);
  };

  const submitRule = async () => {
    const values = await ruleForm.validateFields();
    if (editingRule) {
      await api.updateRule(editingRule.id, values);
      message.success('规则更新成功');
    } else {
      await api.createRule(values);
      message.success('规则创建成功');
    }
    setRuleOpen(false);
    await loadRulesAndProviders(selectedRobotId);
  };

  const toggleRuleEnabled = async (row: Rule, enabled: boolean) => {
    setRuleSwitchLoading((prev) => [...prev, row.id]);
    try {
      await api.updateRule(row.id, { enabled });
      setRules((prev) => prev.map((r) => (r.id === row.id ? { ...r, enabled } : r)));
      message.success(`规则已${enabled ? '启用' : '停用'}`);
    } finally {
      setRuleSwitchLoading((prev) => prev.filter((id) => id !== row.id));
    }
  };

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card title="WorkTool 平台设置">
        <Form form={worktoolForm} layout="inline" onFinish={submitWorktoolSettings}>
          <Form.Item
            name="worktool_api_base"
            label="WorkTool 后台地址"
            rules={[{ required: true, message: '请输入后台地址' }]}
            tooltip="支持官方 SaaS 或私有化地址，例如 https://api.worktool.ymdyes.cn"
            style={{ marginBottom: 8, minWidth: 520 }}
          >
            <Input placeholder="https://api.worktool.ymdyes.cn" style={{ width: 460 }} />
          </Form.Item>
          <Form.Item
            name="callback_public_base_url"
            label="回调公网基础地址"
            tooltip={(
              <div style={{ maxWidth: 520 }}>
                <div>请填写可被 WorkTool 服务器访问的公网地址。</div>
                <div style={{ marginTop: 6 }}>
                  当前访问地址：{callbackBaseSuggestions.current_request_base || '未探测到'}
                </div>
                <div style={{ marginTop: 6 }}>
                  若未探测到公网地址，请使用域名/Nginx/网关暴露后的公网地址填写。回调 URL 为
                  http(s)://&lt;你的公网域名&gt;/api/v1/callback/qa/&lt;robot_id&gt;。
                </div>
              </div>
            )}
            style={{ marginBottom: 8, minWidth: 520 }}
          >
            <Input placeholder="例如 https://bot.example.com" style={{ width: 460 }} />
          </Form.Item>
          <Form.Item name="auto_bind_message_callback_on_create" valuePropName="checked" label="新建自动绑定" style={{ marginBottom: 8 }}>
            <Switch />
          </Form.Item>
          <Form.Item style={{ marginBottom: 8 }}>
            <Button onClick={() => void copyWorktoolBase()}>复制</Button>
          </Form.Item>
          <Form.Item style={{ marginBottom: 8 }}>
            <Button htmlType="submit" type="primary" loading={savingWorktool}>保存</Button>
          </Form.Item>
          {callbackExampleUrl ? (
            <Form.Item style={{ marginBottom: 0, marginTop: -4, width: '100%' }}>
              <Typography.Text type="secondary">回调示例：{callbackExampleUrl}</Typography.Text>
            </Form.Item>
          ) : null}
        </Form>
      </Card>

      <Card title="机器人配置" extra={<Button type="primary" onClick={onCreateRobot}>新建机器人</Button>}>
        <Collapse
          defaultActiveKey={[]}
          items={[
            {
              key: 'robot-list',
              label: `机器人列表（当前：${selectedRobotId || '-'}）`,
              children: (
                <Table
                  rowKey="robot_id"
                  dataSource={items}
                  pagination={false}
                  columns={[
                    { title: 'Robot ID', dataIndex: 'robot_id' },
                    { title: '名称', dataIndex: 'name' },
                    { title: '群聊', dataIndex: 'group_chat_enabled', render: (v) => (v ? '开' : '关') },
                    { title: '私聊', dataIndex: 'private_chat_enabled', render: (v) => (v ? '开' : '关') },
                    { title: '仅@回复', dataIndex: 'group_reply_only_when_mentioned', render: (v) => (v ? '是' : '否') },
                    {
                      title: '操作',
                      render: (_, row: Robot) => (
                        <Space>
                          <Button size="small" onClick={() => setSelectedRobotId(row.robot_id)}>选择</Button>
                          <Button size="small" onClick={() => onEditRobot(row)}>编辑</Button>
                          <Popconfirm
                            title="确认删除该机器人？"
                            description={`robot_id: ${row.robot_id}`}
                            okText="删除"
                            cancelText="取消"
                            okButtonProps={{ danger: true }}
                            onConfirm={() => void onDeleteRobot(row)}
                          >
                            <Button size="small" danger>删除</Button>
                          </Popconfirm>
                        </Space>
                      )
                    }
                  ]}
                />
              )
            }
          ]}
        />
      </Card>

      <Card
        title={`规则管理${selectedRobotId ? `（${selectedRobotId}）` : ''}`}
        extra={(
          <Space>
            <Select
              style={{ width: 340 }}
              value={selectedRobotId}
              onChange={setSelectedRobotId}
              options={robotOptions}
              placeholder="选择机器人"
              showSearch
              optionFilterProp="label"
            />
            <Button type="primary" onClick={onCreateRule} disabled={!selectedRobotId}>新增规则</Button>
          </Space>
        )}
      >
        <Tabs
          activeKey={ruleScene}
          onChange={(k) => setRuleScene(k as 'group' | 'private')}
          items={[
            { key: 'group', label: '群聊规则' },
            { key: 'private', label: '私聊规则' }
          ]}
        />
        <Table
          rowKey="id"
          dataSource={sceneRules}
          pagination={false}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 80 },
            { title: '正则规则', dataIndex: 'pattern' },
            { title: 'Provider', dataIndex: 'provider_name' },
            { title: '优先级', dataIndex: 'priority', width: 100 },
            {
              title: '启用',
              width: 90,
              render: (_, row: Rule) => (
                <Switch
                  size="small"
                  checked={row.enabled}
                  loading={ruleSwitchLoading.includes(row.id)}
                  onChange={(checked) => void toggleRuleEnabled(row, checked)}
                />
              )
            },
            {
              title: '排序',
              width: 120,
              render: (_, row: Rule) => (
                <Space>
                  <Button size="small" icon={<ArrowUpOutlined />} onClick={() => onMoveRule(row, -1)} />
                  <Button size="small" icon={<ArrowDownOutlined />} onClick={() => onMoveRule(row, 1)} />
                </Space>
              )
            },
            {
              title: '操作',
              render: (_, row: Rule) => (
                <Space>
                  <Tag color={row.scene === 'group' ? 'blue' : 'purple'}>{row.scene}</Tag>
                  <Button size="small" onClick={() => onEditRule(row)}>编辑</Button>
                </Space>
              )
            }
          ]}
        />
      </Card>

      <Modal
        title={editingRobot ? '编辑机器人' : '新建机器人'}
        open={robotOpen}
        onCancel={() => setRobotOpen(false)}
        onOk={submitRobot}
        destroyOnClose
        width={720}
      >
        <Form form={robotForm} layout="vertical">
          <Form.Item name="robot_id" label="Robot ID" rules={[{ required: true }]}>
            <Input disabled={!!editingRobot} />
          </Form.Item>
          <Form.Item name="name" label="名称（选填）">
            <Input />
          </Form.Item>
          <Form.Item name="group_default_reply" label="群聊默认回复">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="private_default_reply" label="私聊默认回复">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Space>
            <Form.Item name="group_chat_enabled" valuePropName="checked" label="群聊开关">
              <Switch />
            </Form.Item>
            <Form.Item name="private_chat_enabled" valuePropName="checked" label="私聊开关">
              <Switch />
            </Form.Item>
            <Form.Item name="group_reply_only_when_mentioned" valuePropName="checked" label="仅@回复">
              <Switch />
            </Form.Item>
          </Space>
        </Form>
      </Modal>

      <Modal
        title={editingRule ? '编辑规则' : '新增规则'}
        open={ruleOpen}
        onCancel={() => setRuleOpen(false)}
        onOk={submitRule}
        destroyOnClose
        width={680}
      >
        <Form form={ruleForm} layout="vertical">
          <Form.Item name="robot_id" label="Robot ID" rules={[{ required: true }]}>
            <Select options={items.map((r) => ({ label: `${r.name} (${r.robot_id})`, value: r.robot_id }))} />
          </Form.Item>
          <Form.Item name="scene" label="场景" rules={[{ required: true }]}>
            <Select
              options={[
                { label: '群聊', value: 'group' },
                { label: '私聊', value: 'private' }
              ]}
            />
          </Form.Item>
          <Form.Item name="pattern" label="匹配规则（正则）" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="provider_id"
            label={(
              <Space size={8}>
                <span>Provider</span>
                <Button size="small" type="link" style={{ padding: 0, height: 'auto' }} onClick={() => navigate('/providers')}>
                  新增 Provider
                </Button>
              </Space>
            )}
            rules={[{ required: true }]}
          >
            <Select options={providerOptions} />
          </Form.Item>
          <Form.Item name="priority" label="优先级" rules={[{ required: true }]}>
            <InputNumber min={1} max={9999} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="enabled" valuePropName="checked" label="启用">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
