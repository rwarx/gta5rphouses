import React, { useState, useEffect } from 'react';
import { Card, Typography, Table, Tag, Button, Spin, Descriptions, Row, Col, Statistic } from 'antd';
import { ReloadOutlined, PlayCircleOutlined } from '@ant-design/icons';

const ScraperStatus = ({ api }) => {
    const [status, setStatus] = useState(null);
    const [loading, setLoading] = useState(true);
    const [scraping, setScraping] = useState(false);

    useEffect(() => {
        fetchStatus();
        const interval = setInterval(fetchStatus, 10000);
        return () => clearInterval(interval);
    }, []);

    const fetchStatus = async () => {
        try {
            const res = await api.get('/api/v1/scraper/status');
            setStatus(res.data);
        } catch (err) {
            console.error('Failed to fetch scraper status:', err);
        }
        setLoading(false);
    };

    const triggerScrape = async () => {
        setScraping(true);
        try {
            // Side-effecting endpoint: POST + bearer token. The token is read
            // from the build-time env (REACT_APP_ADMIN_TOKEN); without it the
            // backend returns 503 (endpoint disabled).
            const token = process.env.REACT_APP_ADMIN_TOKEN;
            await api.post('/api/v1/scraper/trigger', null, {
                headers: token ? { Authorization: `Bearer ${token}` } : {},
            });
        } catch (err) {
            console.error('Failed to trigger scrape:', err);
        }
        setTimeout(() => setScraping(false), 5000);
    };

    if (loading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;

    const columns = [
        { title: 'Время', dataIndex: 'ran_at', key: 'time', render: (t) => t ? new Date(t).toLocaleString('ru-RU') : '—' },
        {
            title: 'Статус',
            dataIndex: 'status',
            key: 'status',
            render: (s) => (
                <Tag color={s === 'success' ? 'green' : s === 'partial' ? 'orange' : 'red'}>
                    {s === 'success' ? '✅ Успех' : s === 'partial' ? '⚠️ Частично' : '❌ Ошибка'}
                </Tag>
            ),
        },
        { title: 'Проверено', dataIndex: 'apartments_checked', key: 'checked' },
        { title: 'Успешно', dataIndex: 'apartments_success', key: 'success' },
        { title: 'Ошибок', dataIndex: 'apartments_failed', key: 'failed' },
        { title: 'Изменений', dataIndex: 'changes_detected', key: 'changes' },
        { title: 'Длительность', dataIndex: 'duration_seconds', key: 'duration', render: (d) => d ? `${d.toFixed(1)}с` : '—' },
        { title: 'Payday', dataIndex: 'is_payday_run', key: 'payday', render: (p) => p ? '🎯 Да' : '—' },
    ];

    return (
        <div>
            <Typography.Title level={3}>🤖 Состояние парсера</Typography.Title>

            <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
                <Col xs={24} md={12}>
                    <Card>
                        <Statistic
                            title="Всего запусков"
                            value={status?.statistics?.total_runs || 0}
                            prefix="🔄"
                        />
                    </Card>
                </Col>
                <Col xs={24} md={12}>
                    <Card>
                        <Statistic
                            title="Успешность"
                            value={status?.statistics?.success_rate || 0}
                            suffix="%"
                            prefix="📈"
                            valueStyle={{ color: (status?.statistics?.success_rate || 0) > 80 ? '#52c41a' : '#ff4d4f' }}
                        />
                    </Card>
                </Col>
            </Row>

            <Card
                title="📋 Последние запуски"
                extra={
                    <Button
                        type="primary"
                        icon={<PlayCircleOutlined />}
                        onClick={triggerScrape}
                        loading={scraping}
                    >
                        Запустить парсер
                    </Button>
                }
            >
                <Table
                    dataSource={status?.recent_logs || []}
                    columns={columns}
                    rowKey="id"
                    pagination={{ pageSize: 10 }}
                    size="small"
                    locale={{ emptyText: 'Нет записей о запусках парсера' }}
                />
            </Card>
        </div>
    );
};

export default ScraperStatus;