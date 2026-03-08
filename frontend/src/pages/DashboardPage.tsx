import { useEffect, useMemo, useState } from 'react';
import { Card, Col, Row, Statistic, Typography } from 'antd';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { api } from '../api';

interface TrendItem {
  date: string;
  inbound: number;
  outbound_success: number;
}

export default function DashboardPage() {
  const [overview, setOverview] = useState<any>(null);
  const [trends, setTrends] = useState<TrendItem[]>([]);

  useEffect(() => {
    api.getOverview().then(setOverview);
    api.getTrends(7).then((res) => setTrends(res.items || []));
  }, []);

  const stats = useMemo(
    () => [
      { title: '机器人总数', value: overview?.robots_total ?? 0 },
      { title: '今日入站消息', value: overview?.inbound_today ?? 0 },
      { title: '今日成功回复', value: overview?.outbound_success_today ?? 0 },
      { title: '发送失败率', value: `${((overview?.fail_rate ?? 0) * 100).toFixed(2)}%` }
    ],
    [overview]
  );

  return (
    <>
      <Typography.Title level={5}>运营概览</Typography.Title>
      <Row gutter={[16, 16]}>
        {stats.map((item) => (
          <Col span={6} key={item.title}>
            <Card>
              <Statistic title={item.title} value={item.value} />
            </Card>
          </Col>
        ))}
      </Row>

      <Card style={{ marginTop: 16 }} title="近7天消息趋势">
        <ResponsiveContainer width="100%" height={320}>
          <LineChart data={trends}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="date" />
            <YAxis />
            <Tooltip />
            <Legend />
            <Line type="monotone" dataKey="inbound" stroke="#1677ff" name="入站" />
            <Line type="monotone" dataKey="outbound_success" stroke="#52c41a" name="成功回复" />
          </LineChart>
        </ResponsiveContainer>
      </Card>
    </>
  );
}
