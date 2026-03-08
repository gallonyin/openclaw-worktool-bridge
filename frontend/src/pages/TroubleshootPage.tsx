import { useMemo, useState } from 'react';
import { Alert, Button, Card, Col, Descriptions, Input, InputNumber, Row, Space, Table, Tag, Typography, message } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { api } from '../api';

type ResultData = {
  input: Record<string, any>;
  resolved: Record<string, any>;
  sections: Record<string, any>;
  diagnostics: string[];
} | null;

export default function TroubleshootPage() {
  const [robotId, setRobotId] = useState('');
  const [messageId, setMessageId] = useState('');
  const [keyword, setKeyword] = useState('');
  const [startTime, setStartTime] = useState('');
  const [endTime, setEndTime] = useState('');
  const [limit, setLimit] = useState(20);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ResultData>(null);

  const runSearch = async () => {
    setLoading(true);
    try {
      const data = await api.troubleshootSearch({
        robot_id: robotId.trim(),
        message_id: messageId.trim(),
        keyword: keyword.trim(),
        start_time: startTime.trim(),
        end_time: endTime.trim(),
        limit
      });
      setResult(data);
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '查询失败');
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  const qaRows = useMemo(() => result?.sections?.['问答回调记录'] || [], [result]);
  const rawMessageRows = useMemo(() => result?.sections?.['raw_message_record 指令发送记录表'] || [], [result]);
  const rawConfirmRows = useMemo(() => result?.sections?.['raw_msg_confirm 指令客户端执行结果表'] || [], [result]);
  const callbackRows = useMemo(() => result?.sections?.['回调配置'] || [], [result]);
  const onlineRows = useMemo(() => result?.sections?.['上线记录(最多20条)'] || [], [result]);
  const localLogRows = useMemo(() => result?.sections?.['本地消息处理记录'] || [], [result]);
  const robotStatus = result?.sections?.['机器人状态'] || {};

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card title="机器人排查">
        <Row gutter={12}>
          <Col span={8}>
            <Input value={robotId} onChange={(e) => setRobotId(e.target.value)} placeholder="robot_id（可选）" />
          </Col>
          <Col span={8}>
            <Input value={messageId} onChange={(e) => setMessageId(e.target.value)} placeholder="message_id（可选）" />
          </Col>
          <Col span={8}>
            <Input value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="关键词（可选）" />
          </Col>
        </Row>
        <Row gutter={12} style={{ marginTop: 12 }}>
          <Col span={8}>
            <Input value={startTime} onChange={(e) => setStartTime(e.target.value)} placeholder="开始时间（如 2026-03-01 00:00:00）" />
          </Col>
          <Col span={8}>
            <Input value={endTime} onChange={(e) => setEndTime(e.target.value)} placeholder="结束时间（如 2026-03-01 23:59:59）" />
          </Col>
          <Col span={4}>
            <InputNumber min={1} max={100} value={limit} onChange={(v) => setLimit(Number(v || 20))} style={{ width: '100%' }} />
          </Col>
          <Col span={4}>
            <Button type="primary" icon={<SearchOutlined />} loading={loading} onClick={runSearch} block>
              查询
            </Button>
          </Col>
        </Row>
      </Card>

      {result?.diagnostics?.length ? (
        <Card title="诊断提示">
          <Space direction="vertical" style={{ width: '100%' }}>
            {result.diagnostics.map((x, idx) => (
              <Alert key={`${x}-${idx}`} type="warning" showIcon message={x} />
            ))}
          </Space>
        </Card>
      ) : null}

      {result ? (
        <Card title="查询概览">
          <Descriptions column={2} size="small" bordered>
            <Descriptions.Item label="输入 robot_id">{result.input?.robot_id || '-'}</Descriptions.Item>
            <Descriptions.Item label="输入 message_id">{result.input?.message_id || '-'}</Descriptions.Item>
            <Descriptions.Item label="解析后 robot_id">{result.resolved?.robot_id || '-'}</Descriptions.Item>
            <Descriptions.Item label="由 message_id 反查">{result.resolved?.message_resolved_robot ? '是' : '否'}</Descriptions.Item>
          </Descriptions>
        </Card>
      ) : null}

      {result ? (
        <Card title="机器人状态">
          <Descriptions column={2} size="small" bordered>
            {Object.entries(robotStatus).map(([k, v]) => (
              <Descriptions.Item key={k} label={k}>
                {typeof v === 'boolean' ? <Tag color={v ? 'blue' : 'red'}>{v ? '是' : '否'}</Tag> : String(v ?? '-')}
              </Descriptions.Item>
            ))}
          </Descriptions>
        </Card>
      ) : null}

      {result ? (
        <Card title={`回调配置 (${callbackRows.length})`}>
          <Table
            rowKey={(_, idx) => String(idx)}
            pagination={false}
            dataSource={callbackRows}
            columns={[
              { title: '回调类型', dataIndex: '回调类型' },
              { title: '回调地址', dataIndex: '回调地址', ellipsis: true },
              { title: '类型编号', dataIndex: '类型编号', width: 100 }
            ]}
          />
        </Card>
      ) : null}

      {result ? (
        <Card title={`上线记录 (${onlineRows.length})`}>
          <Table
            rowKey={(_, idx) => String(idx)}
            pagination={false}
            dataSource={onlineRows}
            columns={[
              { title: '上线时间', dataIndex: '上线时间' },
              { title: '下线时间', dataIndex: '下线时间' },
              { title: '在线时长(分钟)', dataIndex: '在线时长(分钟)' },
              { title: '登录IP', dataIndex: '登录IP' }
            ]}
          />
        </Card>
      ) : null}

      {result ? (
        <Card title={`raw_message_record 指令发送记录表 (${rawMessageRows.length})`}>
          <Table
            rowKey={(_, idx) => String(idx)}
            dataSource={rawMessageRows}
            pagination={false}
            scroll={{ x: 1200 }}
            columns={[
              { title: '时间', dataIndex: '时间', width: 180 },
              { title: '消息ID', dataIndex: '消息ID', width: 220, ellipsis: true },
              { title: '接收对象', dataIndex: '接收对象', width: 220, ellipsis: true },
              { title: '发送内容', dataIndex: '发送内容', ellipsis: true },
              { title: '消息类型', dataIndex: '消息类型', width: 100 },
              { title: '状态', dataIndex: '状态', width: 100 }
            ]}
          />
        </Card>
      ) : null}

      {result ? (
        <Card title={`raw_msg_confirm 指令客户端执行结果表 (${rawConfirmRows.length})`}>
          <Table
            rowKey={(_, idx) => String(idx)}
            dataSource={rawConfirmRows}
            pagination={false}
            columns={[
              { title: '时间', dataIndex: '时间', width: 180 },
              { title: '消息ID', dataIndex: '消息ID', width: 220, ellipsis: true },
              { title: '执行结果', dataIndex: '执行结果', width: 100 },
              { title: '执行耗时(秒)', dataIndex: '执行耗时(秒)', width: 120 },
              { title: '失败原因', dataIndex: '失败原因', ellipsis: true }
            ]}
          />
        </Card>
      ) : null}

      {result ? (
        <Card title={`问答回调记录 (${qaRows.length})`}>
          <Table
            rowKey={(_, idx) => String(idx)}
            dataSource={qaRows}
            pagination={false}
            scroll={{ x: 1400 }}
            columns={[
              { title: '时间', dataIndex: '时间', width: 180 },
              { title: '提问者', dataIndex: '提问者', width: 120 },
              { title: '会话', dataIndex: '会话', width: 180, ellipsis: true },
              { title: '问题', dataIndex: '问题', width: 300, ellipsis: true },
              { title: '回答', dataIndex: '回答', width: 300, ellipsis: true },
              { title: '消息ID', dataIndex: '消息ID', width: 220, ellipsis: true }
            ]}
          />
        </Card>
      ) : null}

      {result ? (
        <Card title={`本地消息处理记录 (${localLogRows.length})`}>
          <Table
            rowKey={(_, idx) => String(idx)}
            dataSource={localLogRows}
            pagination={false}
            columns={[
              { title: '时间', dataIndex: '时间', width: 180 },
              { title: '方向', dataIndex: '方向', width: 80 },
              { title: '场景', dataIndex: '场景', width: 80 },
              { title: '会话', dataIndex: '会话', width: 180, ellipsis: true },
              { title: '消息', dataIndex: '消息', ellipsis: true },
              { title: '状态', dataIndex: '状态', width: 100 }
            ]}
          />
          <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
            已去除 SQL 与敏感字段，仅保留排查必要信息。
          </Typography.Paragraph>
        </Card>
      ) : null}
    </Space>
  );
}
