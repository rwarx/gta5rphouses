import React, { useState } from 'react';
import { Table, Input, Tag, Typography, Card, Button, Space } from 'antd';
import { SearchOutlined, HistoryOutlined } from '@ant-design/icons';

const ApartmentList = ({ apartments, onSelect, api, showHistory }) => {
    const [searchText, setSearchText] = useState('');

    const filtered = apartments.filter(apt =>
        !searchText ||
        apt.name.toLowerCase().includes(searchText.toLowerCase()) ||
        (apt.address && apt.address.toLowerCase().includes(searchText.toLowerCase()))
    );

    const columns = [
        {
            title: 'Название',
            dataIndex: 'name',
            key: 'name',
            render: (text, record) => (
                <Button type="link" onClick={() => onSelect(record)}>
                    🏠 {text}
                </Button>
            ),
            sorter: (a, b) => a.name.localeCompare(b.name),
        },
        {
            title: 'Адрес',
            dataIndex: 'address',
            key: 'address',
            render: (text) => text || '—',
            responsive: ['md'],
        },
        {
            title: 'Всего',
            dataIndex: 'total_apartments',
            key: 'total',
            width: 80,
            sorter: (a, b) => (a.total_apartments || 0) - (b.total_apartments || 0),
        },
        {
            title: 'Свободно',
            dataIndex: 'free_apartments',
            key: 'free',
            width: 100,
            sorter: (a, b) => (a.free_apartments || 0) - (b.free_apartments || 0),
            render: (text) => (
                <Tag color={text > 0 ? 'green' : 'red'} style={{ fontSize: 14, padding: '2px 12px' }}>
                    {text || 0}
                </Tag>
            ),
        },
        {
            title: 'Занято',
            dataIndex: 'occupied_apartments',
            key: 'occupied',
            width: 80,
            render: (text) => text || 0,
        },
        {
            title: 'Обновлено',
            dataIndex: 'last_updated',
            key: 'updated',
            width: 160,
            responsive: ['lg'],
            render: (text) => text ? new Date(text).toLocaleString('ru-RU') : '—',
            sorter: (a, b) => new Date(a.last_updated || 0) - new Date(b.last_updated || 0),
            defaultSortOrder: 'descend',
        },
    ];

    return (
        <div>
            <Typography.Title level={3}>
                {showHistory ? '📋 История изменений' : '🏠 Список квартир'}
            </Typography.Title>
            <Card>
                <Space style={{ marginBottom: 16 }}>
                    <Input
                        placeholder="🔍 Поиск по названию или адресу..."
                        prefix={<SearchOutlined />}
                        value={searchText}
                        onChange={e => setSearchText(e.target.value)}
                        style={{ width: 350 }}
                        allowClear
                    />
                    <Tag color="green">🟢 Свободно: {apartments.filter(a => a.free_apartments > 0).length}</Tag>
                    <Tag color="red">🔴 Занято: {apartments.filter(a => a.free_apartments === 0 && a.total_apartments > 0).length}</Tag>
                </Space>
                <Table
                    dataSource={filtered}
                    columns={columns}
                    rowKey="id"
                    pagination={{ pageSize: 20, showSizeChanger: true }}
                    size="small"
                    locale={{ emptyText: 'Нет данных о квартирах' }}
                />
            </Card>
        </div>
    );
};

export default ApartmentList;