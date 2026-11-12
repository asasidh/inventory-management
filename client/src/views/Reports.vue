<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { api } from '../api'
import { useFilters } from '../composables/useFilters'
import { useI18n } from '../composables/useI18n'
import { formatCurrencyWithDecimals } from '../utils/currency'

const { t, currentCurrency } = useI18n()

const loading = ref(true)
const error = ref(null)
const quarterlyData = ref([])
const monthlyData = ref([])

// Use shared filters
const {
  selectedPeriod,
  selectedLocation,
  selectedCategory,
  selectedStatus,
  getCurrentFilters
} = useFilters()

// Request-id guard: filter changes can trigger overlapping loads, and a slow
// response for an older filter set must not overwrite the newer one.
let latestRequestId = 0

const loadData = async () => {
  const requestId = ++latestRequestId
  loading.value = true
  error.value = null
  try {
    const filters = getCurrentFilters()
    const [quarterly, monthly] = await Promise.all([
      api.getQuarterlyReports(filters),
      api.getMonthlyTrends(filters)
    ])
    if (requestId !== latestRequestId) return
    quarterlyData.value = quarterly
    monthlyData.value = monthly
  } catch (err) {
    if (requestId !== latestRequestId) return
    console.error('Failed to load reports:', err)
    error.value = t('reports.loadFailed', { message: err.message })
  } finally {
    if (requestId === latestRequestId) loading.value = false
  }
}

onMounted(loadData)

// Watch for filter changes and reload data
watch([selectedPeriod, selectedLocation, selectedCategory, selectedStatus], loadData)

const MONTH_KEYS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']

const hasData = computed(() => quarterlyData.value.length > 0 || monthlyData.value.length > 0)

const money = (value) => formatCurrencyWithDecimals(value, currentCurrency.value, 2)

// Summary stats derived from the loaded data
const totalRevenue = computed(() =>
  monthlyData.value.reduce((sum, m) => sum + m.revenue, 0)
)

const avgMonthlyRevenue = computed(() =>
  monthlyData.value.length > 0 ? totalRevenue.value / monthlyData.value.length : 0
)

const totalOrders = computed(() =>
  monthlyData.value.reduce((sum, m) => sum + m.order_count, 0)
)

const bestQuarter = computed(() => {
  let best = null
  for (const q of quarterlyData.value) {
    if (!best || q.total_revenue > best.total_revenue) best = q
  }
  return best ? formatQuarter(best.quarter) : t('reports.notAvailable')
})

const maxRevenue = computed(() =>
  monthlyData.value.reduce((max, m) => Math.max(max, m.revenue), 0)
)

// Month-over-month change and growth, computed once per row
const monthlyRows = computed(() =>
  monthlyData.value.map((month, index) => {
    if (index === 0) return { ...month, hasPrevious: false }
    const previous = monthlyData.value[index - 1].revenue
    const change = month.revenue - previous
    const rate = previous === 0 ? null : (change / previous) * 100
    return {
      ...month,
      hasPrevious: true,
      changeText: formatChange(change),
      growthText: rate === null
        ? t('reports.notAvailable')
        : `${rate > 0 ? '+' : ''}${rate.toFixed(1)}%`,
      changeClass: change > 0 ? 'positive-change' : change < 0 ? 'negative-change' : ''
    }
  })
)

function formatChange(change) {
  if (change > 0) return '+' + money(change)
  if (change < 0) return '-' + money(Math.abs(change))
  return money(0)
}

// "2025-03" -> localized month label
function formatMonth(monthStr) {
  const [year, month] = monthStr.split('-')
  const key = MONTH_KEYS[parseInt(month, 10) - 1]
  if (!key) return monthStr
  return t('reports.monthLabel', { month: t(`months.${key}`), year })
}

// API quarter labels look like "Q1-2025"
function formatQuarter(label) {
  const match = /^Q(\d)-(\d{4})$/.exec(label)
  if (!match) return label
  return t('reports.quarterLabel', { quarter: match[1], year: match[2] })
}

const getBarHeight = (revenue) => {
  if (maxRevenue.value === 0) return 0
  // Max bar height is 200px
  return (revenue / maxRevenue.value) * 200
}

const getFulfillmentClass = (rate) => {
  if (rate >= 90) return 'badge success'
  if (rate >= 75) return 'badge warning'
  return 'badge danger'
}
</script>

<template>
  <div class="reports">
    <div class="page-header">
      <h2>{{ t('reports.title') }}</h2>
      <p>{{ t('reports.subtitle') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('reports.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else-if="!hasData" class="card empty-state">{{ t('reports.noData') }}</div>
    <div v-else>
      <!-- Quarterly Performance -->
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('reports.quarterlyPerformance') }}</h3>
        </div>
        <div class="table-container">
          <table class="reports-table">
            <thead>
              <tr>
                <th>{{ t('reports.quarter') }}</th>
                <th>{{ t('reports.totalOrders') }}</th>
                <th>{{ t('reports.totalRevenue') }}</th>
                <th>{{ t('reports.avgOrderValue') }}</th>
                <th>{{ t('reports.fulfillmentRate') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="q in quarterlyData" :key="q.quarter">
                <td><strong>{{ formatQuarter(q.quarter) }}</strong></td>
                <td>{{ q.total_orders }}</td>
                <td>{{ money(q.total_revenue) }}</td>
                <td>{{ money(q.avg_order_value) }}</td>
                <td>
                  <span :class="getFulfillmentClass(q.fulfillment_rate)">
                    {{ q.fulfillment_rate }}%
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Monthly Trends Chart -->
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('reports.monthlyRevenueTrend') }}</h3>
        </div>
        <div class="chart-container">
          <div class="bar-chart">
            <div v-for="month in monthlyData" :key="month.month" class="bar-wrapper">
              <div class="bar-container">
                <div
                  class="bar"
                  :style="{ height: getBarHeight(month.revenue) + 'px' }"
                  :title="money(month.revenue)"
                ></div>
              </div>
              <div class="bar-label">{{ formatMonth(month.month) }}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- Month-over-Month Comparison -->
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('reports.monthOverMonth') }}</h3>
        </div>
        <div class="table-container">
          <table class="reports-table">
            <thead>
              <tr>
                <th>{{ t('reports.month') }}</th>
                <th>{{ t('reports.orders') }}</th>
                <th>{{ t('reports.revenue') }}</th>
                <th>{{ t('reports.change') }}</th>
                <th>{{ t('reports.growthRate') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in monthlyRows" :key="row.month">
                <td><strong>{{ formatMonth(row.month) }}</strong></td>
                <td>{{ row.order_count }}</td>
                <td>{{ money(row.revenue) }}</td>
                <td>
                  <span v-if="row.hasPrevious" :class="row.changeClass">{{ row.changeText }}</span>
                  <span v-else>-</span>
                </td>
                <td>
                  <span v-if="row.hasPrevious" :class="row.changeClass">{{ row.growthText }}</span>
                  <span v-else>-</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Summary Stats -->
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">{{ t('reports.totalRevenue') }}</div>
          <div class="stat-value">{{ money(totalRevenue) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('reports.avgMonthlyRevenue') }}</div>
          <div class="stat-value">{{ money(avgMonthlyRevenue) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('reports.totalOrders') }}</div>
          <div class="stat-value">{{ totalOrders }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('reports.bestQuarter') }}</div>
          <div class="stat-value">{{ bestQuarter }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.reports {
  padding: 0;
}

.card {
  background: var(--surface);
  border-radius: 12px;
  padding: 1.5rem;
  margin-bottom: 1.5rem;
  box-shadow: var(--shadow);
}

.card-header {
  margin-bottom: 1.5rem;
}

.card-title {
  font-family: var(--font-display);
  font-size: 1.25rem;
  font-weight: 600;
  color: var(--text);
  margin: 0;
}

.reports-table {
  width: 100%;
  border-collapse: collapse;
}

.reports-table th {
  background: var(--surface-2);
  padding: 0.75rem;
  text-align: left;
  font-weight: 600;
  color: var(--text-muted);
  border-bottom: 2px solid var(--border);
}

.reports-table td {
  padding: 0.75rem;
  border-bottom: 1px solid var(--border);
}

.reports-table tr:hover {
  background: var(--surface-2);
}

.chart-container {
  padding: 2rem 1rem;
  min-height: 300px;
}

.bar-chart {
  display: flex;
  align-items: flex-end;
  justify-content: space-around;
  height: 250px;
  gap: 0.5rem;
}

.bar-wrapper {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: 1;
  max-width: 80px;
}

.bar-container {
  height: 200px;
  display: flex;
  align-items: flex-end;
  width: 100%;
}

.bar {
  width: 100%;
  background: var(--chart-1);
  border-radius: 4px 4px 0 0;
  transition: all 0.3s;
  cursor: pointer;
}

.bar:hover {
  background: var(--accent-hover);
}

.bar-label {
  margin-top: 1.5rem;
  font-size: 0.75rem;
  color: var(--text-muted);
  text-align: center;
  transform: rotate(-45deg);
  white-space: nowrap;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 1rem;
  margin-top: 1.5rem;
}

.stat-card {
  background: var(--surface);
  border-radius: 12px;
  padding: 1.5rem;
  box-shadow: var(--shadow);
  border-left: 4px solid var(--accent);
}

.stat-label {
  font-size: 0.875rem;
  color: var(--text-muted);
  margin-bottom: 0.5rem;
}

.stat-value {
  font-family: var(--font-display);
  font-size: 1.875rem;
  font-weight: 700;
  color: var(--text);
}

.badge {
  padding: 0.25rem 0.75rem;
  border-radius: 9999px;
  font-size: 0.875rem;
  font-weight: 500;
}

.badge.success {
  background: var(--success-soft);
  color: var(--success);
}

.badge.warning {
  background: var(--warning-soft);
  color: var(--warning);
}

.badge.danger {
  background: var(--danger-soft);
  color: var(--danger);
}

.positive-change {
  color: var(--success);
  font-weight: 600;
}

.negative-change {
  color: var(--danger);
  font-weight: 600;
}

.loading {
  text-align: center;
  padding: 3rem;
  color: var(--text-muted);
}

.error {
  background: var(--danger-soft);
  color: var(--danger);
  padding: 1rem;
  border-radius: 8px;
  margin: 1rem 0;
}
</style>

.empty-state {
  text-align: center;
  padding: 3rem;
  color: var(--text-muted);
}
