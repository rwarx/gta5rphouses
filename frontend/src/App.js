import React, { useState, useEffect } from 'react';
import { ConfigProvider, Layout, Menu, Typography, theme } from 'antd';
import {
    HomeOutlined,
    BarChartOutlined,
    HistoryOutlined,
    SettingOutlined,
    ApiOutlined,
} from '@ant-design/icons';
import ApartmentList from './components/ApartmentList';
import ApartmentDetail from './components/ApartmentDetail';
import Dashboard from './components/Dashboard';
import ScraperStatus from './components/ScraperStatus';
import axios from 'axios';

const { Header, Sider, Content } = Layout;
const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

const App = () => {
    const [collapsed, setCollapsed] = useState(false);
    const [selectedMenu, setSelectedMenu] = useState('dashboard');
    const [apartments, setApartments] = useState([]);
    const [stats, setStats] = useState(null);
    const [loading, setLoading] = useState(false);
    const [selectedApartment, setSelectedApartment] = useState(null);

    const api = axios.create({ baseURL: API_URL });

    useEffect(() => {
        fetchData();
        const interval = setInterval(fetchData, 30000);
        return () => clearInterval(interval);
    }, []);

    const fetchData = async () => {
        try {
            const [aptRes, statsRes] = await Promise.all([
                api.get('/api/v1/apartments'),
                api.get('/api/v1/statistics'),
            ]);
            setApartments(aptRes.data);
            setStats(statsRes.data);
        } catch (err) {
            console.error('Failed to fetch data:', err);
        }
    };

    const menuItems = [
        { key: 'dashboard', icon: <BarChartOutlined />, label: 'Дашборд' },
        { key: 'apartments', icon: <HomeOutlined />, label: 'Квартиры' },
        { key: 'history', icon: <HistoryOutlined />, label: 'История' },
        { key: 'scraper', icon: <ApiOutlined />, label: 'Парсер' },
    ];

    const renderContent = () => {
        switch (selectedMenu) {
            case 'dashboard':
                return <Dashboard stats={stats} apartments={apartments} api={api} />;
            case 'apartments':
                return selectedApartment ? (
                    <ApartmentDetail
                        apartment={selectedApartment}
                        api={api}
                        onBack={() => setSelectedApartment(null)}
                    />
                ) : (
                    <ApartmentList
                        apartments={apartments}
                        onSelect={setSelectedApartment}
                        api={api}
                    />
                );
            case 'history':
                return <ApartmentList apartments={apartments} onSelect={setSelectedApartment} api={api} showHistory />;
            case 'scraper':
                return <ScraperStatus api={api} />;
            default:
                return <Dashboard stats={stats} apartments={apartments} api={api} />;
        }
    };

    return (
        <ConfigProvider
            theme={{
                algorithm: theme.darkAlgorithm,
                token: { colorPrimary: '#1677ff', borderRadius: 8 },
            }}
        >
            <Layout style={{ minHeight: '100vh' }}>
                <Sider
                    collapsible
                    collapsed={collapsed}
                    onCollapse={setCollapsed}
                    theme="dark"
                >
                    <div style={{ padding: 16, textAlign: 'center' }}>
                        <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
                            {collapsed ? '🏠' : '🏠 Apt Checker'}
                        </Typography.Title>
                    </div>
                    <Menu
                        theme="dark"
                        selectedKeys={[selectedMenu]}
                        items={menuItems}
                        onClick={({ key }) => setSelectedMenu(key)}
                    />
                </Sider>
                <Layout>
                    <Header style={{ padding: '0 24px', background: '#141414' }}>
                        <Typography.Title level={4} style={{ color: '#fff', margin: '12px 0' }}>
                            GTA5RP Apartment Monitor - Murrieta
                        </Typography.Title>
                    </Header>
                    <Content style={{ padding: 24, overflow: 'auto' }}>
                        {renderContent()}
                    </Content>
                </Layout>
            </Layout>
        </ConfigProvider>
    );
};

export default App;