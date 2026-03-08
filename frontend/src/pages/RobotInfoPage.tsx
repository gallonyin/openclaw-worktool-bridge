import { useEffect, useMemo, useState } from 'react';
import { Button, Card, Col, Input, Modal, Row, Select, Space, Table, Tabs, Tag, Typography, message } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { Robot } from '../types';
import { SELECTED_ROBOT_STORAGE_KEY } from '../constants';

interface CallbackItem {
  type: number;
  typeName: string;
  callBackUrl: string;
}

type CallbackTabKey = 'message' | 'command' | 'group_qr' | 'online_status';

const tabToType: Record<CallbackTabKey, number | null> = {
  message: 11,
  command: 1,
  group_qr: 0,
  online_status: 5
};

function fmtTime(ms?: number) {
  if (!ms) return '';
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString();
}

function fmtDateTimeText(val?: string) {
  if (!val) return '';
  const d = new Date(val);
  if (Number.isNaN(d.getTime())) return val;
  return d.toLocaleString();
}

function parseLooseDateTime(val?: string) {
  if (!val) return 0;
  const d1 = new Date(val);
  if (!Number.isNaN(d1.getTime())) return d1.getTime();
  const d2 = new Date(val.replace(' ', 'T'));
  if (!Number.isNaN(d2.getTime())) return d2.getTime();
  return 0;
}

function isNotExpired(val?: string) {
  if (!val) return false
  const d = new Date(val);
  if (Number.isNaN(d.getTime())) return false;
  return d.getTime() > Date.now();
}

export default function RobotInfoPage() {
  const [robots, setRobots] = useState<Robot[]>([]);
  const [selectedRobotId, setSelectedRobotId] = useState<string | undefined>(() => {
    try {
      return localStorage.getItem(SELECTED_ROBOT_STORAGE_KEY) || undefined;
    } catch {
      return undefined;
    }
  });
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<any>(null);
  const [callbacks, setCallbacks] = useState<CallbackItem[]>([]);
  const [online, setOnline] = useState<boolean | null>(null);
  const [onlineInfos, setOnlineInfos] = useState<any[]>([]);

  const [tabKey, setTabKey] = useState<CallbackTabKey>('message');
  const [callbackInput, setCallbackInput] = useState('');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [deletingType, setDeletingType] = useState<number | null>(null);

  const robotOptions = useMemo(
    () => robots.map((r) => ({ label: `${r.name} (${r.robot_id})`, value: r.robot_id })),
    [robots]
  );

  const callbackMap = useMemo(() => {
    const m = new Map<number, string>();
    callbacks.forEach((x) => m.set(x.type, x.callBackUrl));
    return m;
  }, [callbacks]);

  const syncInputByTab = (nextTabKey: CallbackTabKey) => {
    if (nextTabKey === 'online_status') {
      setCallbackInput(callbackMap.get(5) || callbackMap.get(6) || '');
      return;
    }
    const tp = tabToType[nextTabKey];
    setCallbackInput(tp === null ? '' : callbackMap.get(tp) || '');
  };

  const loadBaseRobots = async () => {
    const items = await api.listRobots();
    setRobots(items);
    if (items.length === 0) {
      setSelectedRobotId(undefined);
      return;
    }
    const current = selectedRobotId;
    const exists = current && items.some((x: Robot) => x.robot_id === current);
    if (!exists) {
      setSelectedRobotId(items[0].robot_id);
    }
  };

  const loadInfo = async (robotId?: string) => {
    if (!robotId) return;
    setLoading(true);
    try {
      const [detailRes, callbackRes, onlineRes, onlineInfosRes] = await Promise.all([
        api.getRobotInfoDetail(robotId),
        api.getRobotInfoCallbacks(robotId),
        api.getRobotInfoOnline(robotId),
        api.getRobotInfoOnlineInfos(robotId)
      ]);
      const detailData = detailRes?.data || null;
      const callbackData = callbackRes?.data || [];
      const onlineInfosData = [...(onlineInfosRes?.data || [])]
        .sort((a: any, b: any) => parseLooseDateTime(b?.onlineTime) - parseLooseDateTime(a?.onlineTime))
        .slice(0, 20);
      setDetail(detailData);
      setCallbacks(callbackData);
      setOnline(onlineRes?.data === true);
      setOnlineInfos(onlineInfosData);
      const m = new Map<number, string>();
      callbackData.forEach((x: CallbackItem) => m.set(x.type, x.callBackUrl));
      if (tabKey === 'online_status') {
        setCallbackInput(m.get(5) || m.get(6) || '');
      } else {
        const tp = tabToType[tabKey];
        setCallbackInput(tp === null ? '' : m.get(tp) || '');
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '拉取机器人信息失败');
      setDetail(null);
      setCallbacks([]);
      setOnline(null);
      setOnlineInfos([]);
      setCallbackInput('');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadBaseRobots();
  }, []);

  useEffect(() => {
    void loadInfo(selectedRobotId);
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

  const onlineText = online === true ? '在线' : '不在线';
  const onlineTagColor = online === true ? 'blue' : 'red';
  const isValid = detail?.robotType !== 4 && isNotExpired(detail?.authExpir || detail?.authExpire);
  const validText = isValid ? '有效' : '无效';
  const validTagColor = isValid ? 'blue' : 'red';
  const firstLoginText = fmtDateTimeText(detail?.firstLogin) || fmtTime(detail?.signatureTime) || '-';
  const authExpirText = fmtDateTimeText(detail?.authExpir || detail?.authExpire) || '-';
  const latestOnlineInfo = [...onlineInfos].sort(
    (a, b) => parseLooseDateTime(b?.onlineTime) - parseLooseDateTime(a?.onlineTime)
  )[0];
  const lastLoginIp = latestOnlineInfo?.ip || detail?.ip || detail?.lastLoginIp || '-';

  const ensureCallbackInput = () => {
    if (!selectedRobotId) {
      message.warning('请先选择机器人');
      return false;
    }
    if (!callbackInput.trim()) {
      message.warning('请输入消息回调地址');
      return false;
    }
    return true;
  };

  const onTestCallback = async () => {
    if (!ensureCallbackInput()) return;
    setTesting(true);
    try {
      if (tabKey === 'message') {
        await api.testRobotMessageCallback(selectedRobotId as string, callbackInput.trim());
      } else {
        await api.testRobotCallback2xx(callbackInput.trim());
      }
      message.success('测试通过');
    } catch (e: any) {
      Modal.error({
        title: '测试失败',
        content: e?.response?.data?.detail || e?.message || '未知错误',
      });
    } finally {
      setTesting(false);
    }
  };

  const onSaveCallback = async () => {
    if (!ensureCallbackInput()) return;
    setSaving(true);
    try {
      const robotId = selectedRobotId as string;
      const url = callbackInput.trim();
      if (tabKey === 'message') {
        await api.bindRobotMessageCallback(robotId, url);
      } else if (tabKey === 'online_status') {
        await api.bindRobotCallbackType(robotId, url, 5);
        await api.bindRobotCallbackType(robotId, url, 6);
      } else {
        const callbackType = tabToType[tabKey];
        if (callbackType === null) {
          throw new Error('不支持的回调类型');
        }
        await api.bindRobotCallbackType(robotId, url, callbackType);
      }
      message.success('绑定成功');
      await loadInfo(selectedRobotId);
    } catch (e: any) {
      Modal.error({
        title: '绑定失败',
        content: e?.response?.data?.detail || e?.message || '未知错误',
      });
    } finally {
      setSaving(false);
    }
  };

  const callbackDoc = (() => {
    if (tabKey === 'message') {
      return { label: '消息回调说明', url: 'https://worktool.apifox.cn/doc-861677' };
    }
    if (tabKey === 'command') {
      return { label: '指令回调说明', url: 'https://worktool.apifox.cn/api-44952776' };
    }
    if (tabKey === 'group_qr') {
      return { label: '群二维码回调说明', url: '' };
    }
    return { label: '上下线回调说明', url: '' };
  })();

  const onOpenCallbackDoc = () => {
    if (tabKey === 'group_qr') {
      Modal.info({
        title: '群二维码回调说明',
        content: '创建群和修改群配置指令执行时回调 每次都是最新的码7天有效 另：app进设置-高级设置-打开获取群二维码',
      });
      return;
    }
    if (tabKey === 'online_status') {
      Modal.info({
        title: '上下线回调说明',
        content: '创建一个企微内部群-右上角添加消息推送，然后复制webhook地址来绑定即可生效',
      });
      return;
    }
    window.open(callbackDoc.url, '_blank', 'noopener,noreferrer');
  };

  const onDeleteCallback = (row: CallbackItem) => {
    if (!selectedRobotId) {
      message.warning('请先选择机器人');
      return;
    }
    Modal.confirm({
      title: '确认删除回调',
      content: `将删除【${row.typeName}】配置，是否继续？`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        setDeletingType(row.type);
        try {
          await api.deleteRobotCallbackByType(selectedRobotId, row.type);
          message.success('删除成功');
          await loadInfo(selectedRobotId);
        } catch (e: any) {
          Modal.error({
            title: '删除失败',
            content: e?.response?.data?.detail || e?.message || '未知错误',
          });
        } finally {
          setDeletingType(null);
        }
      },
    });
  };

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title="机器人基本信息"
        loading={loading}
        extra={(
          <Select
            style={{ width: 340 }}
            value={selectedRobotId}
            onChange={setSelectedRobotId}
            options={robotOptions}
            placeholder="更换机器人"
            showSearch
            optionFilterProp="label"
          />
        )}
      >
        <table className="robot-info-matrix">
          <tbody>
            <tr>
              <th>机器人编号</th>
              <td>{detail?.robotId || '-'}</td>
              <th>是否在线</th>
              <td><Tag color={onlineTagColor}>{onlineText}</Tag></td>
              <th>ip</th>
              <td>{lastLoginIp}</td>
            </tr>
            <tr>
              <th>是否有效</th>
              <td><Tag color={validTagColor}>{validText}</Tag></td>
              <th>识别账号昵称</th>
              <td>{detail?.name || '-'}</td>
              <th>企业</th>
              <td>{detail?.corporation || '-'}</td>
            </tr>
            <tr>
              <th>启用时间</th>
              <td>{firstLoginText}</td>
              <th>到期时间</th>
              <td>{authExpirText}</td>
              <th>消息回调</th>
              <td>
                {detail?.openCallback === 1 ? <Tag color="blue">开启</Tag> : <Tag>关闭</Tag>}
              </td>
            </tr>
          </tbody>
        </table>
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card
            title="机器人回调配置信息"
            loading={loading}
            extra={<Typography.Link onClick={() => loadInfo(selectedRobotId)}><ReloadOutlined /> 刷新</Typography.Link>}
          >
            <Table
              rowKey={(r) => `${r.type}-${r.callBackUrl}`}
              dataSource={callbacks}
              pagination={false}
              tableLayout="fixed"
              columns={[
                {
                  title: '回调类型',
                  dataIndex: 'typeName',
                  width: 160,
                  render: (v: string) => <span style={{ whiteSpace: 'nowrap' }}>{v}</span>
                },
                {
                  title: '回调地址',
                  dataIndex: 'callBackUrl',
                  render: (v: string) => <span style={{ wordBreak: 'break-all' }}>{v}</span>
                },
                {
                  title: '操作',
                  width: 100,
                  align: 'center',
                  render: (_: any, row: CallbackItem) => (
                    <Button
                      danger
                      size="small"
                      loading={deletingType === row.type}
                      onClick={() => onDeleteCallback(row)}
                    >
                      删除
                    </Button>
                  )
                }
              ]}
              locale={{ emptyText: '暂无回调配置' }}
            />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="机器人登录日志" extra={<Typography.Link>查看更多</Typography.Link>}>
            <Table
              rowKey={(r: any, idx) => `${r.robotId || 'r'}-${r.onlineTime || ''}-${idx}`}
              dataSource={onlineInfos}
              pagination={false}
              columns={[
                { title: '登录ip', dataIndex: 'ip' },
                { title: '上线时间', dataIndex: 'onlineTime' },
                {
                  title: '下线时间',
                  render: (_: any, row: any) => row.offline || row.offlineTime || '-'
                },
                { title: '在线总时长(分钟)', dataIndex: 'onlineTimes' }
              ]}
              locale={{ emptyText: '暂无数据' }}
            />
          </Card>
        </Col>
      </Row>

      <Card className="robot-callback-config" bodyStyle={{ paddingTop: 8 }}>
        <div className="callback-title">回调配置</div>
        <Tabs
          activeKey={tabKey}
          onChange={(k) => {
            const key = k as CallbackTabKey;
            setTabKey(key);
            syncInputByTab(key);
          }}
          items={[
            { key: 'message', label: '消息回调' },
            { key: 'command', label: '指令执行结果回调' },
            { key: 'group_qr', label: '群二维码回调' },
            { key: 'online_status', label: '机器人上下线回调' }
          ]}
        />
        <div className="callback-form-row">
          <label>
            <span className="required">*</span> 回调地址
          </label>
          <Input
            value={callbackInput}
            onChange={(e) => setCallbackInput(e.target.value)}
            placeholder="请输入回调地址"
          />
        </div>
        <div className="callback-action-row">
          <Button type="primary" loading={saving} onClick={onSaveCallback}>保存设置</Button>
          <Button type="link" loading={testing} onClick={onTestCallback}>测试回调地址</Button>
          <Button type="link" onClick={onOpenCallbackDoc}>
            {callbackDoc.label}
          </Button>
        </div>
      </Card>
    </Space>
  );
}
