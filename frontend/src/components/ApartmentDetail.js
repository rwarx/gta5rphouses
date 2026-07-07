import React, { useState, useEffect } from 'react';
import { Card, Descriptions, Tag, Typography, Button, Spin, Row, Col, Divider, Timeline } from 'antd';
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';

const ApartmentDetail = ({ apartment, api, onBack }) => {
    const [history, setHistory] = useState([]);
    const [freeHistory, setFreeHistory] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        fetchHistory();
    }, [apartment.id]);

    const fetchHistory = async () => {
        setLoading(true);
        try {
            const [histRes, freeRes] = await Promise.all([
                api.get(`/api/v1/apartments/${apartment.id}/history?days=1`),
                api.get(`/api/v1/apartments/${apartment.id}/free-history?days=7`),
            ]);
            setHistory(histRes.data);
            setFreeHistory(freeRes.data);
        } catch (err) {
            console.error('Failed to fetch history:', err);
        }
        setLoading(false);
    };

    return (
        <div>
            <Button icon={<ArrowLeftOutlined />} onClick={onBack} style={{ marginBottom: 16 }}>
                Назад к списку
            </Button>

            <Row gutter={16}>
                <Col xs={24} lg={14}>
                    <Card
                        title={`🏠 ${apartment.name}`}
                        extra={<Button icon={<ReloadOutlined />} onClick={fetchHistory} />}
                    >
                        <Descriptions column={{ xs: 1, sm: 2 }} bordered size="small">
                            <Descriptions.Item label="Адрес">{apartment.address || '—'}</Descriptions.Item>
                            <Descriptions.Item label="Всего">{apartment.total_apartments || '—'}</Descriptions.Item>
                            <Descriptions.Item label="Свободно">
                                <Tag color={apartment.free_apartments > 0 ? 'green' : 'red'} style={{ fontSize: 16 }}>
                                    {apartment.free_apartments || 0}
                                </Tag>
                            </Descriptions.Item>
                            <Descriptions.Item label="Занято">{apartment.occupied_apartments || 0}</Descriptions.Item>
                            {apartment.description && (
                                <Descriptions.Item label="Описание" span={2}>
                                    {apartment.description}
                                </Descriptions.Item>
                            )}
                            <Descriptions.Item label="Обновлено">
                                {apartment.last_updated
                                    ? new Date(apartment.last_updated).toLocaleString('ru-RU')
                                    : '—'}
                            </Descriptions.Item>
                            {apartment.wiki_url && (
                                <Descriptions.Item label="Wiki">
                                    <a href={apartment.wiki_url} target="_blank" rel="noreferrer">
                                        Открыть Wiki
                                    </a>
                                </Descriptions.Item>
                            )}
                        </Descriptions>

                        {apartment.types && apartment.types.length > 0 && (
                            <>
                                <Divider>По классам</Divider>
                                {apartment.types.map((type, idx) => (
                                    <Card key={idx} size="small" style={{ marginBottom: 8 }}>
                                        <Row justify="space-between">
                                            <Col><strong>{type.class_name}</strong></Col>
                                            <Col>
                                                <Tag color={type.free > 0 ? 'green' : 'red'}>
                                                    {type.free || 0} свободно
                                                </Tag>
                                                <Tag>{type.occupied || 0} занято</Tag>
                                                {type.total && <Tag>всего {type.total}</Tag>}
                                            </Col>
                                        </Row>
                                    </Card>
                                ))}
                            </>
                        )}
                    </Card>
                </Col>

                <Col xs={24} lg={10}>
                    <Card title="📈 График свободных мест (7 дней)">
                        {freeHistory.length > 0 ? (
                            <ResponsiveContainer width="100%" height={250}>
                                <LineChart data={freeHistory}>
                                    <CartesianGrid strokeDasharray="3 3" stroke="#303030" />
                                    <XAxis
                                        dataKey="time"
                                        tick={{ fontSize: 10 }}
                                        tickFormatter={(val) => new Date(val).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit' })}
                                    />
                                    <YAxis allowDecimals={false} />
                                    <Tooltip
                                        labelFormatter={(val) => new Date(val).toLocaleString('ru-RU')}
                                        formatter={(value) => [value, 'Свободно']}
                                    />
                                    <Line
                                        type="monotone"
                                        dataKey="free"
                                        stroke="#52c41a"
                                        strokeWidth={2}
                                        dot={false}
                                    />
                                </LineChart>
                            </ResponsiveContainer>
                        ) : (
                            <Typography.Text type="secondary">Нет данных для графика</Typography.Text>
                        )}
                    </Card>

                    <Card title="📋 Последние изменения" style={{ marginTop: 16 }}>
                        {loading ? (
                            <Spin />
                        ) : history.slice(0, 10).length > 0 ? (
                            <Timeline
                                items={history.slice(0, 10).map((h, idx) => ({
                                    color: h.free_apartments > 0 ? 'green' : 'red',
                                    children: (
                                        <div>
                                            <div style={{ fontSize: 12, color: '#888' }}>
                                                {new Date(h.recorded_at).toLocaleString('ru-RU')}
                                            </div>
                                            <div>Свободно: {h.free_apartments || 0}</div>
                                        </div>
                                    ),
                                }))}
                            />
                        ) : (
                            <Typography.Text type="secondary">История изменений пуста</Typography.Text>
                        )}
                    </Card>
                </Col>
            </Row>
        </div>
    );
};

export default ApartmentDetail;