import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Typography, message } from 'antd';
import { api } from '../api';
import type { ForwardLog, ForwardRule, Robot } from '../types';
import { getLastSelectedRobotId, setLastSelectedRobotId } from '../robotSelection';

function modeOptions() {
  return [
    { label: '全部', value: 'all' },
    { label: '精准匹配', value: 'exact' },
    { label: '模糊匹配', value: 'regex' },
  ];
}

export default function ForwardPage() {
  const [robots, setRobots] = useState<Robot[]>([]);
  const [robotsLoaded, setRobotsLoaded] = useState(false);
  const [rules, setRules] = useState<ForwardRule[]>([]);
  const [logs, setLogs] = useState<ForwardLog[]>([]);
  const [sourceRobotFilter, setSourceRobotFilter] = useState<string | undefined>(() => getLastSelectedRobotId());
  const [logsPage, setLogsPage] = useState(1);
  const [logsPageSize, setLogsPageSize] = useState(20);
  const [logsTotal, setLogsTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [logsLoading, setLogsLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ForwardRule | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  const robotOptions = useMemo(
    () => robots.map((r) => ({ label: r.name ? `${r.name} (${r.robot_id})` : r.robot_id, value: r.robot_id })),
    [robots]
  );

  const loadRobots = async () => {
    try {
      const list = await api.listRobots();
      setRobots(list);
      setRobotsLoaded(true);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载机器人失败');
      setRobots([]);
      setRobotsLoaded(true);
    }
  };

  const selectSourceRobot = (robotId?: string) => {
    setSourceRobotFilter(robotId);
    setLastSelectedRobotId(robotId);
  };

  const loadRules = async () => {
    setLoading(true);
    try {
      const data = await api.listForwardRules(sourceRobotFilter ? { source_robot_id: sourceRobotFilter } : undefined);
      setRules(data || []);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载转发规则失败');
      setRules([]);
    } finally {
      setLoading(false);
    }
  };

  const loadLogs = async (page = logsPage, pageSize = logsPageSize) => {
    setLogsLoading(true);
    try {
      const res = await api.listForwardLogs({
        robot_id: sourceRobotFilter,
        page,
        page_size: pageSize,
      });
      setLogs(res?.items || []);
      setLogsTotal(res?.total || 0);
      setLogsPage(res?.page || page);
      setLogsPageSize(res?.page_size || pageSize);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载转发日志失败');
      setLogs([]);
      setLogsTotal(0);
    } finally {
      setLogsLoading(false);
    }
  };

  const reloadAll = async () => {
    await Promise.all([loadRules(), loadLogs(1, logsPageSize)]);
  };

  useEffect(() => {
    void loadRobots();
  }, []);

  useEffect(() => {
    if (!robotsLoaded) return;
    if (robots.length === 0) {
      selectSourceRobot(undefined);
      return;
    }
    const current = sourceRobotFilter;
    const exists = current && robots.some((x) => x.robot_id === current);
    if (!exists) {
      selectSourceRobot(robots[0].robot_id);
    }
  }, [robotsLoaded, robots, sourceRobotFilter]);

  useEffect(() => {
    void reloadAll();
  }, [sourceRobotFilter]);

  const openCreate = () => {
    if (!sourceRobotFilter) {
      message.warning('请先在页面上方选择来源机器人，再新增转发规则');
      return;
    }
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({
      source_robot_id: sourceRobotFilter,
      source_scene: 'group',
      source_match_type: 'all',
      use_other_robot: false,
      prefix_enabled: true,
      prefix_template: '',
      keyword_match_type: 'all',
      enabled: true,
    });
    setModalOpen(true);
  };

  const openEdit = (row: ForwardRule) => {
    setEditing(row);
    form.setFieldsValue({
      source_robot_id: row.source_robot_id,
      source_scene: row.source_scene,
      source_match_type: row.source_match_type,
      source_pattern: row.source_pattern,
      target_name: row.target_name,
      use_other_robot: row.use_other_robot,
      send_robot_id: row.send_robot_id,
      prefix_enabled: row.prefix_enabled,
      prefix_template: row.prefix_template,
      keyword_match_type: row.keyword_match_type,
      keyword_pattern: row.keyword_pattern,
      enabled: row.enabled,
    });
    setModalOpen(true);
  };

  const submit = async () => {
    const values = await form.validateFields();
    if (!editing && !sourceRobotFilter) {
      message.warning('请先在页面上方选择来源机器人');
      return;
    }
    if (!editing) {
      values.source_robot_id = sourceRobotFilter;
    } else {
      values.source_robot_id = editing.source_robot_id;
    }
    values.source_pattern = (values.source_pattern || '').trim();
    values.target_name = (values.target_name || '').trim();
    values.prefix_template = (values.prefix_template || '').trim();
    values.keyword_pattern = (values.keyword_pattern || '').trim();

    if (values.source_match_type !== 'all' && !values.source_pattern) {
      message.warning('来源对象匹配为精准/模糊时，请填写来源对象');
      return;
    }
    if (values.keyword_match_type !== 'all' && !values.keyword_pattern) {
      message.warning('关键词匹配为精准/模糊时，请填写关键词');
      return;
    }
    if (values.use_other_robot && !values.send_robot_id) {
      message.warning('已开启“使用其他机器人发送”，请先选择发送机器人');
      return;
    }

    setSaving(true);
    try {
      if (editing) {
        await api.updateForwardRule(editing.id, values);
        message.success('转发规则更新成功');
      } else {
        await api.createForwardRule(values);
        message.success('转发规则创建成功');
      }
      setModalOpen(false);
      await reloadAll();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async (row: ForwardRule) => {
    try {
      await api.deleteForwardRule(row.id);
      message.success('转发规则已删除');
      await reloadAll();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '删除失败');
    }
  };

  const onToggleEnabled = async (row: ForwardRule, enabled: boolean) => {
    try {
      await api.updateForwardRule(row.id, { enabled });
      setRules((prev) => prev.map((x) => (x.id === row.id ? { ...x, enabled } : x)));
      message.success(`规则已${enabled ? '启用' : '停用'}`);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '更新状态失败');
    }
  };

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="当前仅支持文本消息转发"
        description="机器人收到的文本消息可转发到目标名称（群名或用户备注名）。"
      />

      <Card
        title="消息转发规则"
        extra={(
          <Space>
            <Select
              style={{ width: 320 }}
              value={sourceRobotFilter}
              onChange={selectSourceRobot}
              options={robotOptions}
              placeholder="按来源机器人筛选"
              showSearch
              optionFilterProp="label"
            />
            <Button type="primary" onClick={openCreate}>新增转发规则</Button>
          </Space>
        )}
      >
        <Table
          size="small"
          rowKey="id"
          loading={loading}
          dataSource={rules}
          pagination={false}
          columns={[
            {
              title: '启用',
              width: 70,
              render: (_, row: ForwardRule) => <Switch size="small" checked={row.enabled} onChange={(v) => void onToggleEnabled(row, v)} />,
            },
            { title: '来源场景', dataIndex: 'source_scene', width: 82, render: (v: string) => (v === 'group' ? '群聊' : '私聊') },
            {
              title: '来源对象匹配',
              width: 170,
              ellipsis: true,
              render: (_, row: ForwardRule) => row.source_match_type === 'all' ? '全部来源对象' : `${row.source_match_type === 'exact' ? '精准' : '模糊'}：${row.source_pattern || '-'}`,
            },
            { title: '目标群名/昵称/备注名', dataIndex: 'target_name', width: 160, ellipsis: true },
            {
              title: '发送机器人',
              width: 140,
              ellipsis: true,
              render: (_, row: ForwardRule) => row.use_other_robot ? (row.send_robot_id || '-') : `${row.source_robot_id}（同来源）`,
            },
            {
              title: '前缀',
              width: 180,
              ellipsis: true,
              render: (_, row: ForwardRule) => row.prefix_enabled ? (row.prefix_template || '默认前缀') : '关闭（原文转发）',
            },
            {
              title: '关键词过滤',
              width: 170,
              ellipsis: true,
              render: (_, row: ForwardRule) => row.keyword_match_type === 'all' ? '全部消息' : `${row.keyword_match_type === 'exact' ? '精准' : '模糊'}：${row.keyword_pattern || '-'}`,
            },
            {
              title: '操作',
              width: 120,
              render: (_, row: ForwardRule) => (
                <Space>
                  <Button size="small" onClick={() => openEdit(row)}>编辑</Button>
                  <Popconfirm
                    title="确认删除该转发规则？"
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                    onConfirm={() => void onDelete(row)}
                  >
                    <Button size="small" danger>删除</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Card title={`转发日志（最近 ${logsTotal} 条）`}>
        <Table
          rowKey="id"
          loading={logsLoading}
          dataSource={logs}
          scroll={{ x: 1500 }}
          pagination={{
            current: logsPage,
            pageSize: logsPageSize,
            total: logsTotal,
            showSizeChanger: true,
            onChange: (p, ps) => void loadLogs(p, ps),
          }}
          columns={[
            { title: '时间', dataIndex: 'created_at', width: 180 },
            { title: '来源机器人', dataIndex: 'source_robot_id', width: 130 },
            { title: '发送机器人', dataIndex: 'send_robot_id', width: 130 },
            { title: '来源场景', dataIndex: 'source_scene', width: 90, render: (v: string) => (v === 'group' ? '群聊' : '私聊') },
            { title: '来源对象', dataIndex: 'source_name', width: 160, ellipsis: true },
            { title: '提问者', dataIndex: 'sender_name', width: 120, ellipsis: true },
            { title: '目标', dataIndex: 'target_name', width: 160, ellipsis: true },
            { title: '状态', dataIndex: 'status', width: 100, render: (v: string) => <Tag color={v === 'success' ? 'blue' : v === 'failed' ? 'red' : 'default'}>{v}</Tag> },
            { title: '耗时(秒)', dataIndex: 'time_cost', width: 100, render: (v: number) => (v ?? 0).toFixed(3) },
            { title: '原消息', dataIndex: 'question_text', width: 220, ellipsis: true },
            { title: '转发内容', dataIndex: 'forwarded_text', width: 260, ellipsis: true },
            { title: '失败原因', dataIndex: 'error_reason', width: 260, ellipsis: true },
          ]}
        />
      </Card>

      <Modal
        title={editing ? '编辑转发规则' : '新增转发规则'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => void submit()}
        confirmLoading={saving}
        destroyOnClose
        width={760}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="source_scene" label="来源场景" rules={[{ required: true }]}> 
            <Select options={[{ label: '群聊', value: 'group' }, { label: '私聊', value: 'private' }]} />
          </Form.Item>
          <Form.Item name="source_match_type" label="来源对象匹配" rules={[{ required: true }]}>
            <Select options={modeOptions()} />
          </Form.Item>
          <Form.Item shouldUpdate={(prev, next) => prev.source_match_type !== next.source_match_type} noStyle>
            {({ getFieldValue }) => {
              const m = getFieldValue('source_match_type') || 'all';
              return (
                <Form.Item name="source_pattern" label="来源对象">
                  <Input disabled={m === 'all'} placeholder={m === 'all' ? '全部来源对象无需填写' : '例如：财务群 / 张三'} />
                </Form.Item>
              );
            }}
          </Form.Item>

          <Form.Item name="target_name" label="目标群名/昵称/备注名" rules={[{ required: true, message: '请输入目标群名/昵称/备注名' }]}>
            <Input />
          </Form.Item>

          <Form.Item name="use_other_robot" valuePropName="checked" label="使用其他机器人发送">
            <Switch />
          </Form.Item>
          <Form.Item shouldUpdate={(prev, next) => prev.use_other_robot !== next.use_other_robot} noStyle>
            {({ getFieldValue }) => {
              const on = !!getFieldValue('use_other_robot');
              return (
                <Form.Item name="send_robot_id" label="发送机器人">
                  <Select disabled={!on} options={robotOptions} showSearch optionFilterProp="label" placeholder={on ? '请选择发送机器人(B)' : '默认使用来源机器人'} />
                </Form.Item>
              );
            }}
          </Form.Item>

          <Form.Item name="prefix_enabled" valuePropName="checked" label="启用前缀">
            <Switch />
          </Form.Item>
          <Form.Item shouldUpdate={(prev, next) => prev.prefix_enabled !== next.prefix_enabled} noStyle>
            {({ getFieldValue }) => {
              const on = !!getFieldValue('prefix_enabled');
              return (
                <Form.Item
                  name="prefix_template"
                  label="前缀模板（可选）"
                  tooltip="留空走默认：私聊显示提问者；群聊显示群名+提问者。可用变量：{group_name} {sender_name} {source_name}"
                >
                  <Input disabled={!on} placeholder={on ? '留空使用默认前缀' : '前缀已关闭，将原文转发'} />
                </Form.Item>
              );
            }}
          </Form.Item>

          <Form.Item name="keyword_match_type" label="关键词过滤" rules={[{ required: true }]}> 
            <Select options={modeOptions()} />
          </Form.Item>
          <Form.Item shouldUpdate={(prev, next) => prev.keyword_match_type !== next.keyword_match_type} noStyle>
            {({ getFieldValue }) => {
              const m = getFieldValue('keyword_match_type') || 'all';
              return (
                <Form.Item name="keyword_pattern" label="关键词">
                  <Input disabled={m === 'all'} placeholder={m === 'all' ? '全部消息无需填写' : '例如：报销'} />
                </Form.Item>
              );
            }}
          </Form.Item>

          <Form.Item name="enabled" valuePropName="checked" label="启用规则">
            <Switch />
          </Form.Item>
        </Form>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          当前仅支持文本消息转发。多目标暂不支持（每条规则只转发到一个目标）。
        </Typography.Paragraph>
      </Modal>
    </Space>
  );
}
