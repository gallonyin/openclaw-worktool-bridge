import { useEffect, useMemo, useState } from 'react';
import { ArrowDownOutlined, ArrowUpOutlined } from '@ant-design/icons';
import { Button, Card, Collapse, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Switch, Table, Tabs, Tag, Typography, message } from 'antd';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { Provider, Robot, Rule } from '../types';
import { getLastSelectedRobotId, setLastSelectedRobotId } from '../robotSelection';

export default function RobotPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<Robot[]>([]);
  const [robotsLoaded, setRobotsLoaded] = useState(false);
  const [selectedRobotId, setSelectedRobotId] = useState<string | undefined>(() => getLastSelectedRobotId());
  const [providers, setProviders] = useState<Provider[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [ruleScene, setRuleScene] = useState<'group' | 'private'>('group');

  const [robotOpen, setRobotOpen] = useState(false);
  const [editingRobotId, setEditingRobotId] = useState<string | null>(null);
  const [robotForm] = Form.useForm();

  const [ruleOpen, setRuleOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<Rule | null>(null);
  const [ruleForm] = Form.useForm();
  const [ruleSwitchLoading, setRuleSwitchLoading] = useState<number[]>([]);

  const selectRobot = (robotId?: string) => {
    setSelectedRobotId(robotId);
    setLastSelectedRobotId(robotId);
  };

  const loadRobots = async () => {
    const robots = await api.listRobots();
    setItems(robots);
    setRobotsLoaded(true);
  };

  const loadRulesAndProviders = async (robotId?: string) => {
    if (!robotId) {
      setRules([]);
      setProviders([]);
      return;
    }
    const providerRes = await api.listProviders();
    setProviders(providerRes);
    try {
      const ruleRes = await api.listRules(robotId);
      setRules(ruleRes);
    } catch {
      setRules([]);
    }
  };

  useEffect(() => {
    const init = async () => {
      await loadRobots();
    };
    void init();
  }, []);

  useEffect(() => {
    if (!robotsLoaded) return;
    if (items.length === 0) {
      selectRobot(undefined);
      return;
    }
    const current = selectedRobotId;
    if (!current || !items.some((x) => x.robot_id === current)) {
      selectRobot(items[0].robot_id);
    }
  }, [robotsLoaded, items, selectedRobotId]);

  useEffect(() => {
    void loadRulesAndProviders(selectedRobotId);
  }, [selectedRobotId]);

  const providerOptions = useMemo(
    () => providers.map((p) => ({ label: `${p.name} (${p.id})`, value: p.id })),
    [providers]
  );
  const robotOptions = useMemo(
    () => items.map((r) => ({ label: r.name ? `${r.name} (${r.robot_id})` : r.robot_id, value: r.robot_id })),
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
    setEditingRobotId(null);
    robotForm.resetFields();
    robotForm.setFieldsValue({
      name: '机器人',
      private_chat_enabled: true,
      group_chat_enabled: true,
      group_reply_only_when_mentioned: false
    });
    setRobotOpen(true);
  };

  const onEditRobot = async (robotId: string) => {
    try {
      const res = await api.getRobot(robotId);
      setEditingRobotId(robotId);
      robotForm.resetFields();
      robotForm.setFieldsValue({
        robot_id: robotId,
        name: res?.name || '',
        group_default_reply: res?.defaults?.group || '',
        private_default_reply: res?.defaults?.private || '',
        group_chat_enabled: res?.group_chat_enabled !== false,
        private_chat_enabled: res?.private_chat_enabled !== false,
        group_reply_only_when_mentioned: !!res?.group_reply_only_when_mentioned
      });
      setRobotOpen(true);
    } catch (e: any) {
      Modal.error({
        title: '读取机器人失败',
        content: e?.response?.data?.detail || e?.message || '未知错误',
      });
    }
  };

  const onDeleteRobot = async (row: Robot) => {
    await api.deleteRobot(row.robot_id);
    message.success('机器人已删除');
    await loadRobots();
    if (selectedRobotId === row.robot_id) {
      selectRobot(undefined);
      setRules([]);
    }
  };

  const submitRobot = async () => {
    const values = await robotForm.validateFields();
    try {
      if (editingRobotId) {
        await api.updateRobot(editingRobotId, values);
        await loadRobots();
        message.success('机器人更新成功');
      } else {
        values.name = (values.name || '').trim() || '机器人';
        const res = await api.createRobot(values);
        await loadRobots();
        selectRobot(values.robot_id);
        if (res?.existed) {
          message.success('机器人已存在');
        } else if (res?.auto_bind_message_callback && res?.callback_url) {
          message.success(`机器人创建成功，已自动绑定消息回调：${res.callback_url}`);
        } else {
          message.success('机器人创建成功');
        }
      }
      setRobotOpen(false);
    } catch (e: any) {
      Modal.error({
        title: editingRobotId ? '更新失败' : '创建失败',
        content: e?.response?.data?.detail || e?.message || '未知错误',
      });
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

  const onDeleteRule = async (row: Rule) => {
    await api.deleteRule(row.id);
    message.success('规则已删除');
    await loadRulesAndProviders(selectedRobotId);
  };

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card title="机器人配置" extra={<Button type="primary" onClick={onCreateRobot}>新建机器人</Button>}>
        <Collapse
          defaultActiveKey={[]}
          items={[
            {
              key: 'robot-list',
              label: `机器人列表（当前：${selectedRobotId || '-'}）`,
              children: (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Table
                    rowKey="robot_id"
                    dataSource={items}
                    pagination={false}
                    columns={[
                      { title: 'Robot ID', dataIndex: 'robot_id' },
                      { title: '名称', dataIndex: 'name', render: (v: string) => v || '-' },
                      {
                        title: '操作',
                        render: (_, row: Robot) => (
                          <Space>
                            <Button size="small" onClick={() => selectRobot(row.robot_id)}>选择</Button>
                            <Button size="small" onClick={() => void onEditRobot(row.robot_id)}>编辑</Button>
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
                </Space>
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
                  onChange={selectRobot}
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
                  <Popconfirm
                    title="确认删除该规则？"
                    description={`规则ID: ${row.id}`}
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                    onConfirm={() => void onDeleteRule(row)}
                  >
                    <Button size="small" danger>删除</Button>
                  </Popconfirm>
                </Space>
              )
            }
          ]}
        />
      </Card>

      <Modal
        title={editingRobotId ? '编辑机器人' : '新建机器人'}
        open={robotOpen}
        onCancel={() => setRobotOpen(false)}
        onOk={submitRobot}
        destroyOnClose
        width={720}
      >
        <Form form={robotForm} layout="vertical">
          <Form.Item name="robot_id" label="Robot ID" rules={[{ required: true }]}>
            <Input disabled={!!editingRobotId} />
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
            <Select options={robotOptions} />
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
