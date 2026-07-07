import React from 'react';
import { Row, Col, Card, Statistic, Typography, Spin } from 'antd';
import {
    HomeOutlined,
    CheckCircleOutlined,
    CloseCircleOutlined,
    RiseOutlined,
    ReloadOutlined,
} from '@ant-design/icons';

const Dashboard = ({ stats, apartments, api }) => {
    if (!stats) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;

    const freeApts = apartments.filter(a => a.free_apartments > 0).length;
    const occupiedApts = apartments.filter(a => a.free_apartments === 0 && a.total_apartments > 0).length;

    return (
        <div>
            <Typography.Title level={3}>📊 Дашборд</Typography.Title>
            <Row gutter={[16, 16]}>
                <Col xs={24} sm={12} lg={6}>
                    <Card>
                        <Statistic
                            title="Всего зданий"
                            value={stats.apartments?.total_apartments || 0}
                            prefix={<HomeOutlined />}
                            valueStyle={{ color: '#1677ff' }}
                        />
                    </Card>
                </Col>
                <Col xs={24} sm={12} lg={6}>
                    <Card>
                        <Statistic
                            title="Свободных мест"
                            value={stats.apartments?.total_free || 0}
                            prefix={<CheckCircleOutlined />}
                            valueStyle={{ color: '#52c41a' }}
                        />
                    </Card>
                </Col>
                <Col xs={24} sm={12} lg={6}>
                    <Card>
                        <Statistic
                            title="Занято мест"
                            value={stats.apartments?.total_occupied || 0}
                            prefix={<CloseCircleOutlined />}
                            valueStyle={{ color: '#ff4d4f' }}
                        />
                    </Card>
                </Col>
                <Col xs={24} sm={12} lg={6}>
                    <Card>
                        <Statistic
                            title="Заполненность"
                            value={stats.apartments?.occupancy_rate || 0}
                            suffix="%"
                            prefix={<RiseOutlined />}
                            precision={1}
                            valueStyle={{
                                color: (stats.apartments?.occupancy_rate || 0) > 80 ? '#ff4d4f' : '#52c41a'
                            }}
                        />
                    </Card>
                </Col>
            </Row>

            <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
                <Col xs={24} sm={12} lg={6}>
                    <Card title="🟢 Со свободными">
                        <Typography.Title level={2} style={{ color: '#52c41a', margin: 0 }}>
                            {freeApts}
                        </Typography.Title>
                        <Typography.Text type="secondary">из {apartments.length} зданий</Typography.Text>
                    </Card>
                </Col>
                <Col xs={24} sm={12} lg={6}>
                    <Card title="🔴 Полностью занятые">
                        <Typography.Title level={2} style={{ color: '#ff4d4f', margin: 0 }}>
                            {occupiedApts}
                        </Typography.Title>
                        <Typography.Text type="secondary">из {apartments.length} зданий</Typography.Text>
                    </Card>
                </Col>
                <Col xs={24} sm={12} lg={6}>
                    <Card title="🔄 Успешных запусков">
                        <Typography.Title level={2} style={{ color: '#1677ff', margin: 0 }}>
                            {stats.scraper?.successful_runs || 0}
                        </Typography.Title>
                        <Typography.Text type="secondary">
                           Успешность {stats.scraper?.success_rate || 0}%
                        </Typography.Text>
                    </Card>
                </Col>
                <Col xs={24} sm={12} lg={6}>
                    <Card title="🕐 Последний запуск">
                        <Typography.Text>
                            {stats.scraper?.last_run
                                ? new Date(stats.scraper.last_run).toLocaleString('ru-RU')
                                : 'Нет данных'}
                        </Typography.Text>
                        <br />
                        <Typography.Text type="secondary">
                            Статус: {stats.scraper?.last_run_status || 'N/A'}
                        </Typography.Text>
                    </Card>
                </Col>
            </Row>

            <Card title="📋 Последние изменения" style={{ marginTop: 16 }}>
                {apartments.filter(a => a.last_updated).slice(0, 10).map(apt => (
                    <div key={apt.id} style={{ padding: '8px 0', borderBottom: '1px solid #303030' }}>
                        <strong>{apt.name}</strong> — свободно {apt.free_apartments}/{apt.total_apartments}
                        <span style={{ float: 'right', color: '#888' }}>
                            {new Date(apt.last_updated).toLocaleString('ru-RU')}
                        </span>
                    </div>
                ))}
            </Card>
        </div>
    );
};

export default Dashboard;