import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ArrowDownOutlined, ArrowUpOutlined, QuestionCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Collapse, Form, Input, InputNumber, Modal, Popconfirm, Popover, Select, Space, Switch, Table, Tabs, Tag, Tour, Typography, message } from 'antd';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { Provider, Robot, Rule } from '../types';
import { getLastSelectedRobotId, setLastSelectedRobotId } from '../robotSelection';

function helpLabel(title: string, content: ReactNode) {
  return (
    <Space size={6}>
      <span>{title}</span>
      <Popover content={content} trigger="hover" placement="right">
        <QuestionCircleOutlined style={{ color: '#8c8c8c' }} />
      </Popover>
    </Space>
  );
}

function matchModeLabel(mode?: 'all' | 'exact' | 'regex') {
  if (mode === 'all') return '全部';
  if (mode === 'exact') return '精准匹配';
  return '模糊匹配';
}

function renderRuleMatcher(mode: 'all' | 'exact' | 'regex' | undefined, pattern?: string | null) {
  const m = mode || 'regex';
  if (m === 'all') return '全部';
  return `${matchModeLabel(m)}：${(pattern || '').trim() || '-'}`;
}

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
  const [providerRefreshing, setProviderRefreshing] = useState(false);
  const [showAddRobotTour, setShowAddRobotTour] = useState(false);
  const addRobotBtnRef = useRef<HTMLElement | null>(null);

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

  const refreshProviders = async () => {
    setProviderRefreshing(true);
    try {
      const providerRes = await api.listProviders();
      setProviders(providerRes);
      message.success('AI回复引擎列表已刷新');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '刷新AI回复引擎失败');
    } finally {
      setProviderRefreshing(false);
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

  useEffect(() => {
    if (!robotsLoaded || items.length > 0) return;
    try {
      const tourKey = 'onboarding_add_robot_tour_v1';
      const shown = localStorage.getItem(tourKey) === '1';
      if (!shown) {
        setShowAddRobotTour(true);
        localStorage.setItem(tourKey, '1');
      }
    } catch {
      setShowAddRobotTour(true);
    }
  }, [robotsLoaded, items.length]);

  const providerOptions = useMemo(
    () => providers.map((p) => ({ label: p.name || '未命名引擎', value: p.id })),
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
    try {
      await api.reorderRules(selectedRobotId, ruleScene, list.map((r) => r.id));
      message.success('规则排序已更新');
      await loadRulesAndProviders(selectedRobotId);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '规则排序失败，请刷新后重试；若仍失败请检查该机器人规则是否被并发修改。');
    }
  };

  const onCreateRobot = () => {
    setEditingRobotId(null);
    robotForm.resetFields();
    robotForm.setFieldsValue({
      name: '机器人',
      group_default_reply: '收到',
      private_default_reply: '收到',
      private_chat_enabled: true,
      group_chat_enabled: true,
      group_reply_only_when_mentioned: false,
      group_reply_mode: 'always',
      group_decision_provider_id: undefined
    });
    setRobotOpen(true);
  };

  const onEditRobot = async (robotId: string) => {
    try {
      const res = await api.getRobot(robotId);
      setEditingRobotId(robotId);
      const groupReplyMode = res?.group_reply_mode || (res?.group_reply_only_when_mentioned ? 'mention_only' : 'always');
      robotForm.resetFields();
      robotForm.setFieldsValue({
        robot_id: robotId,
        name: res?.name || '',
        group_default_reply: res?.defaults?.group || '',
        private_default_reply: res?.defaults?.private || '',
        group_chat_enabled: res?.group_chat_enabled !== false,
        private_chat_enabled: res?.private_chat_enabled !== false,
        group_reply_only_when_mentioned: !!res?.group_reply_only_when_mentioned,
        group_reply_mode: groupReplyMode,
        group_decision_provider_id: res?.group_decision_provider_id ?? undefined
      });
      setRobotOpen(true);
    } catch (e: any) {
      Modal.error({
        title: '读取机器人失败',
        content: `${e?.response?.data?.detail || e?.message || '未知错误'}。请先到“机器人信息”页确认该 robot_id 可用，再重试。`,
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
    if (values.group_reply_mode !== 'ai_decide') {
      values.group_decision_provider_id = null;
    }
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
        const baseText = res?.existed ? '机器人已存在，已添加到当前账号' : '机器人创建成功';
        const callbackStatus = String(res?.callback_status || '');
        if (callbackStatus === 'bound' && res?.callback_url) {
          message.success(`${baseText}，已自动绑定默认消息回调：${res.callback_url}`);
        } else if (callbackStatus === 'already_bound') {
          message.success(`${baseText}，检测到已有消息回调，已保持原配置不变。`);
        } else if (callbackStatus === 'no_default_url') {
          message.warning(`${baseText}。系统未配置默认消息回调地址，请到“机器人信息”页手动绑定回调。`);
        } else if (callbackStatus === 'bind_failed') {
          message.warning(`${baseText}，但自动绑定回调失败。请到“机器人信息”页点击测试并手动绑定。`);
        } else {
          message.success(baseText);
        }
      }
      setRobotOpen(false);
    } catch (e: any) {
      Modal.error({
        title: editingRobotId ? '更新失败' : '创建失败',
        content: `${e?.response?.data?.detail || e?.message || '未知错误'}。请检查 Robot ID 是否正确、是否已重复，以及必填项是否完整。`,
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
      pattern_match_type: 'all',
      content_match_type: 'all',
      enabled: true,
      priority: 100
    });
    setRuleOpen(true);
  };

  const onEditRule = (row: Rule) => {
    setEditingRule(row);
    ruleForm.setFieldsValue({
      ...row,
      pattern_match_type: row.pattern_match_type || 'regex',
      content_match_type: row.content_match_type || 'regex',
      pattern: row.pattern || '',
      content_pattern: row.content_pattern || ''
    });
    setRuleOpen(true);
  };

  const submitRule = async () => {
    try {
      const values = await ruleForm.validateFields();
      values.pattern_match_type = values.pattern_match_type || 'regex';
      values.content_match_type = values.content_match_type || 'regex';
      values.pattern = (values.pattern || '').trim();
      values.content_pattern = (values.content_pattern || '').trim();
      if (values.pattern_match_type !== 'all' && !values.pattern) {
        message.warning('群名/昵称匹配方式为精准/模糊时，请填写匹配内容');
        return;
      }
      if (values.content_match_type !== 'all' && !values.content_pattern) {
        message.warning('聊天内容匹配方式为精准/模糊时，请填写匹配内容');
        return;
      }
      if (editingRule) {
        await api.updateRule(editingRule.id, values);
        message.success('规则更新成功');
      } else {
        await api.createRule(values);
        message.success('规则创建成功');
      }
      setRuleOpen(false);
      await loadRulesAndProviders(selectedRobotId);
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || '未知错误';
      Modal.error({
        title: editingRule ? '规则更新失败' : '规则创建失败',
        content: `${detail}。请重点检查“群名/昵称匹配规则（正则）”“聊天内容匹配规则（正则）”和“AI回复引擎”是否正确。`,
      });
    }
  };

  const toggleRuleEnabled = async (row: Rule, enabled: boolean) => {
    setRuleSwitchLoading((prev) => [...prev, row.id]);
    try {
      await api.updateRule(row.id, { enabled });
      setRules((prev) => prev.map((r) => (r.id === row.id ? { ...r, enabled } : r)));
      message.success(`规则已${enabled ? '启用' : '停用'}`);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '更新规则状态失败，请刷新后重试。');
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
      <Card
        title={(
          <Space direction="vertical" size={0}>
            <span>机器人配置</span>
            <Typography.Text type="secondary">设置机器人在哪些情况下回复</Typography.Text>
          </Space>
        )}
        extra={<Button ref={addRobotBtnRef as any} type="primary" onClick={onCreateRobot}>添加机器人</Button>}
      >
        <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
          添加机器人后，系统会自动检测消息回调；如果你还没有配置回调，会默认绑定到本系统回调地址。多数场景下你无需手动修改。
        </Typography.Paragraph>
        {robotsLoaded && items.length === 0 ? (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message="先添加机器人"
            description="当前还没有机器人，这是第一步。点击右上角“添加机器人”，填写 Robot ID 后即可开始配置自动回复。"
            action={<Button size="small" type="primary" onClick={onCreateRobot}>立即添加</Button>}
          />
        ) : null}
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
      <Tour
        open={showAddRobotTour}
        onClose={() => setShowAddRobotTour(false)}
        steps={[
          {
            title: '先添加机器人',
            description: '你还没有机器人，请先点击这里添加。完成后再配置规则和AI回复引擎。',
            target: () => (addRobotBtnRef.current || document.body) as HTMLElement,
          }
        ]}
      />

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
            {
              title: '序号',
              width: 80,
              render: (_: unknown, __: Rule, index: number) => index + 1
            },
            {
              title: '群名/昵称匹配规则',
              render: (_, row: Rule) => renderRuleMatcher(row.pattern_match_type, row.pattern)
            },
            {
              title: '聊天内容匹配规则',
              render: (_, row: Rule) => renderRuleMatcher(row.content_match_type, row.content_pattern)
            },
            { title: 'AI回复引擎', dataIndex: 'provider_name' },
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
                    description={`群名/昵称：${renderRuleMatcher(row.pattern_match_type, row.pattern)}；聊天内容：${renderRuleMatcher(row.content_match_type, row.content_pattern)}`}
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
        title={editingRobotId ? '编辑机器人' : '添加机器人'}
        open={robotOpen}
        onCancel={() => setRobotOpen(false)}
        onOk={submitRobot}
        destroyOnClose
        width={720}
      >
        <Form form={robotForm} layout="vertical">
          <Form.Item
            name="robot_id"
            label={helpLabel(
              'Robot ID',
              <Space direction="vertical" size={4}>
                <div>这是啥：WorkTool 里的机器人唯一编号。</div>
                <div>为什么填：系统靠它识别你要配置哪台机器人。</div>
                <div>怎么填：去 WorkTool 机器人详情页复制后粘贴。</div>
                <div>示例：wtxxxx</div>
              </Space>
            )}
            rules={[{ required: true }]}
          >
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
          </Space>
          <Form.Item
            name="group_reply_mode"
            label={helpLabel(
              '群聊回复策略',
              <Space direction="vertical" size={4}>
                <div>始终回复：@和非@机器人全部根据规则回复。</div>
                <div>仅@回复：@机器人且命中规则时回复。</div>
                <div>AI判断是否需要回复：群里未@时，先由AI判定是否需要机器人回复。</div>
              </Space>
            )}
            rules={[{ required: true, message: '请选择群聊回复策略' }]}
          >
            <Select
              options={[
                { label: '始终回复', value: 'always' },
                { label: '仅@回复', value: 'mention_only' },
                { label: 'AI判断是否需要回复', value: 'ai_decide' }
              ]}
            />
          </Form.Item>
          <Form.Item
            noStyle
            shouldUpdate={(prev, next) => prev.group_reply_mode !== next.group_reply_mode}
          >
            {({ getFieldValue }) => {
              const mode = getFieldValue('group_reply_mode');
              if (mode !== 'ai_decide') return null;
              return (
                <Form.Item
                  name="group_decision_provider_id"
                  label={helpLabel(
                    '群聊判定引擎',
                    <Space direction="vertical" size={4}>
                      <div>用途：仅用于判断“这条群消息要不要回复”。</div>
                      <div>建议：选择一个低成本、低延迟的模型。</div>
                    </Space>
                  )}
                  rules={[{ required: true, message: '请选择群聊判定引擎' }]}
                >
                  <Select
                    options={providerOptions}
                    placeholder="选择AI回复引擎（可在“AI回复引擎”页面先创建）"
                    showSearch
                    optionFilterProp="label"
                  />
                </Form.Item>
              );
            }}
          </Form.Item>
          <Form.Item name="group_reply_only_when_mentioned" valuePropName="checked" hidden initialValue={false}>
            <Switch />
          </Form.Item>
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
          <Form.Item
            name="pattern_match_type"
            label={helpLabel(
              '群名/昵称匹配方式',
              <Space direction="vertical" size={4}>
                <div>这是啥：控制群名/昵称按什么方式命中。</div>
                <div>为什么填：不同场景需要不同的命中精度。</div>
                <div>怎么填：可选全部、精准匹配、模糊匹配。</div>
                <div>示例：精准匹配=财务群；模糊匹配=财务</div>
              </Space>
            )}
            rules={[{ required: true }]}
          >
            <Select
              options={[
                { label: '全部', value: 'all' },
                { label: '精准匹配', value: 'exact' },
                { label: '模糊匹配', value: 'regex' }
              ]}
            />
          </Form.Item>
          <Form.Item
            shouldUpdate={(prev, next) => prev.pattern_match_type !== next.pattern_match_type}
            noStyle
          >
            {({ getFieldValue }) => {
              const mode = getFieldValue('pattern_match_type') || 'regex';
              const disabled = mode === 'all';
              return (
                <Form.Item
                  name="pattern"
                  label={(
                    <Space size={6}>
                      <span>群名/昵称匹配内容</span>
                      <Popover
                        content={(
                          <Space direction="vertical" size={4}>
                            <div>大多数场景直接填关键词即可，可按“包含匹配”理解（类似 like %关键词%）。</div>
                            <div>也支持正则表达式（进阶）：</div>
                            <div>示例1：`财务`（命中“财务群”“上海财务部”）</div>
                            <div>示例2：`^财务群$`（仅精确命中“财务群”）</div>
                            <div>示例3：`(财务|报销).*群`（复杂条件）</div>
                          </Space>
                        )}
                        trigger="hover"
                        placement="right"
                      >
                        <QuestionCircleOutlined style={{ color: '#8c8c8c' }} />
                      </Popover>
                    </Space>
                  )}
                  tooltip={disabled ? '当前为“全部”，无需填写。' : mode === 'exact' ? '将按完全一致匹配。' : '默认按包含匹配理解即可，例如填“财务”就能命中“财务群”。'}
                >
                  <Input disabled={disabled} placeholder={disabled ? '全部匹配时无需填写' : mode === 'exact' ? '例如：财务群' : '例如：财务'} />
                </Form.Item>
              );
            }}
          </Form.Item>
          <Form.Item
            name="content_match_type"
            label={helpLabel(
              '聊天内容匹配方式',
              <Space direction="vertical" size={4}>
                <div>这是啥：控制用户聊天内容按什么方式命中。</div>
                <div>为什么填：可把不同问题路由到不同回复策略。</div>
                <div>怎么填：可选全部、精准匹配、模糊匹配。</div>
                <div>示例：精准匹配=报销流程；模糊匹配=报销</div>
              </Space>
            )}
            rules={[{ required: true }]}
          >
            <Select
              options={[
                { label: '全部', value: 'all' },
                { label: '精准匹配', value: 'exact' },
                { label: '模糊匹配', value: 'regex' }
              ]}
            />
          </Form.Item>
          <Form.Item
            shouldUpdate={(prev, next) => prev.content_match_type !== next.content_match_type}
            noStyle
          >
            {({ getFieldValue }) => {
              const mode = getFieldValue('content_match_type') || 'regex';
              const disabled = mode === 'all';
              return (
                <Form.Item
                  name="content_pattern"
                  label={(
                    <Space size={6}>
                      <span>聊天内容匹配内容</span>
                      <Popover
                        content={(
                          <Space direction="vertical" size={4}>
                            <div>大多数场景直接填关键词即可，可按“包含匹配”理解（类似 like %关键词%）。</div>
                            <div>也支持正则表达式（进阶）：</div>
                            <div>示例1：`报销`（命中“怎么报销”“报销流程”）</div>
                            <div>示例2：`^报销流程$`（仅精确命中“报销流程”）</div>
                            <div>示例3：`(报销|差旅).*标准`（复杂条件）</div>
                          </Space>
                        )}
                        trigger="hover"
                        placement="right"
                      >
                        <QuestionCircleOutlined style={{ color: '#8c8c8c' }} />
                      </Popover>
                    </Space>
                  )}
                  tooltip={disabled ? '当前为“全部”，无需填写。' : mode === 'exact' ? '将按完全一致匹配。' : '默认按包含匹配理解即可；需要时可用正则表达式。'}
                >
                  <Input disabled={disabled} placeholder={disabled ? '全部匹配时无需填写' : mode === 'exact' ? '例如：报销流程' : '例如：报销'} />
                </Form.Item>
              );
            }}
          </Form.Item>
          <Form.Item
            name="provider_id"
            label={(
              <Space size={8}>
                <span>AI回复引擎</span>
                <Button
                  size="small"
                  type="link"
                  style={{ padding: 0, height: 'auto' }}
                  onClick={() => window.open('/providers', '_blank', 'noopener,noreferrer')}
                >
                  新增 AI回复引擎
                </Button>
                <Button
                  size="small"
                  type="link"
                  icon={<ReloadOutlined />}
                  style={{ padding: 0, height: 'auto' }}
                  loading={providerRefreshing}
                  onClick={() => void refreshProviders()}
                >
                  刷新
                </Button>
              </Space>
            )}
            rules={[{ required: true, message: '请选择AI回复引擎' }]}
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
