<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrency, formatCurrencyWithDecimals } from '../utils/currency'

const { t, currentLocale, currentCurrency, translateProductName } = useI18n()

const SLIDER_STEP = 100
const DEFAULT_BUDGET_RATIO = 0.3
const DEBOUNCE_MS = 250

const initialLoading = ref(true)
const initialError = ref(null)

const maxBudget = ref(0)
const budget = ref(0)
const recommendation = ref(null) // latest successful API response
const fetchError = ref(null)

// True from the moment the budget changes until the matching response arrives.
// Covers both the debounce wait and the in-flight request so "Place Order"
// can never submit data that differs from what the user sees.
const pending = ref(false)

const submitting = ref(false)
const placedOrder = ref(null)
const orderError = ref(null)

let debounceTimer = null
let latestRequestId = 0

const items = computed(() => recommendation.value?.items ?? [])

// Round the slider's upper bound up to a whole step. A range input snaps to
// min + n*step, so with max = 95398.48 and step = 100 the thumb would top out
// at 95300 and the user could never afford a full restock.
const sliderMax = computed(() => Math.ceil(maxBudget.value / SLIDER_STEP) * SLIDER_STEP)

// The order lead time is driven by the slowest item in the order.
const orderLeadTime = computed(() =>
  items.value.reduce((max, item) => Math.max(max, item.lead_time_days), 0)
)

const canPlaceOrder = computed(
  () => items.value.length > 0 && !pending.value && !submitting.value && !fetchError.value
)

const fmt = (amount) => formatCurrency(amount, currentCurrency.value)
const fmtUnit = (amount) => formatCurrencyWithDecimals(amount, currentCurrency.value, 2)

const formatDate = (dateString) => {
  const date = new Date(dateString)
  if (isNaN(date.getTime())) return '-'
  return date.toLocaleDateString(currentLocale.value === 'ja' ? 'ja-JP' : 'en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric'
  })
}

const errorMessage = (err) => {
  const detail = err?.response?.data?.detail
  // FastAPI validation errors (422) return detail as an array of objects.
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
  if (typeof detail === 'string') return detail
  return err?.message || 'Unknown error'
}

const fetchRecommendations = async (value) => {
  // Out-of-order guard: every request gets an increasing id and only the
  // most recently issued one may update state. A slow response for an older
  // budget is discarded when it arrives after a newer request was sent.
  const requestId = ++latestRequestId
  try {
    const data = await api.getRestockRecommendations(value)
    if (requestId !== latestRequestId) return
    recommendation.value = data
    fetchError.value = null
  } catch (err) {
    if (requestId !== latestRequestId) return
    // Drop stale items so an order can't be placed from out-of-date data.
    recommendation.value = null
    fetchError.value = t('restocking.loadFailed', { message: errorMessage(err) })
  } finally {
    if (requestId === latestRequestId) pending.value = false
  }
}

// Debounce: restart the timer on every budget change so dragging the slider
// results in a single request after the user pauses.
watch(budget, (value) => {
  pending.value = true
  placedOrder.value = null
  orderError.value = null
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => fetchRecommendations(value), DEBOUNCE_MS)
})

const onNumberInput = (event) => {
  const value = Number(event.target.value)
  if (Number.isNaN(value)) return
  budget.value = Math.min(Math.max(value, 0), sliderMax.value)
}

const placeOrder = async () => {
  if (!canPlaceOrder.value) return
  submitting.value = true
  orderError.value = null
  placedOrder.value = null
  try {
    placedOrder.value = await api.createRestockOrder({
      budget: budget.value,
      items: items.value.map((i) => ({
        item_sku: i.item_sku,
        quantity: i.recommended_quantity
      }))
    })
  } catch (err) {
    orderError.value = t('restocking.orderFailed', { message: errorMessage(err) })
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  try {
    // budget=0 is a cheap call used only to learn max_budget for the slider.
    const probe = await api.getRestockRecommendations(0)
    maxBudget.value = probe.max_budget
    // Default to ~30% of max, snapped to the slider step.
    budget.value = Math.round((probe.max_budget * DEFAULT_BUDGET_RATIO) / SLIDER_STEP) * SLIDER_STEP
  } catch (err) {
    initialError.value = t('restocking.loadFailed', { message: errorMessage(err) })
  } finally {
    initialLoading.value = false
  }
})

onBeforeUnmount(() => {
  clearTimeout(debounceTimer)
  // Invalidate any in-flight request so it cannot touch state after unmount.
  latestRequestId++
})
</script>

<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="initialLoading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="initialError" class="error">{{ initialError }}</div>
    <div v-else>
      <div class="card budget-card">
        <div class="budget-top">
          <div>
            <div class="stat-label">{{ t('restocking.budget') }}</div>
            <div class="budget-value">{{ fmt(budget) }}</div>
          </div>
          <input
            class="budget-number"
            type="number"
            min="0"
            :max="sliderMax"
            :step="SLIDER_STEP"
            :value="budget"
            :aria-label="t('restocking.budget')"
            @change="onNumberInput"
          />
        </div>
        <input
          v-model.number="budget"
          class="budget-slider"
          type="range"
          min="0"
          :max="sliderMax"
          :step="SLIDER_STEP"
          :aria-label="t('restocking.budget')"
        />
        <div class="slider-scale">
          <span>{{ fmt(0) }}</span>
          <span>{{ fmt(maxBudget) }}</span>
        </div>
        <p class="hint">{{ t('restocking.budgetHint') }}</p>
      </div>

      <div v-if="fetchError" class="error">{{ fetchError }}</div>

      <div v-if="recommendation" class="stats-grid" :class="{ stale: pending }">
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.stats.budget') }}</div>
          <div class="stat-value">{{ fmt(recommendation.budget) }}</div>
        </div>
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.stats.recommendedTotal') }}</div>
          <div class="stat-value">{{ fmt(recommendation.total_cost) }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('restocking.stats.remaining') }}</div>
          <div class="stat-value">{{ fmt(recommendation.remaining_budget) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.stats.itemCount') }}</div>
          <div class="stat-value">{{ items.length }}</div>
        </div>
        <div class="stat-card warning">
          <div class="stat-label">{{ t('restocking.stats.leadTime') }}</div>
          <div class="stat-value">{{ t('restocking.days', { count: orderLeadTime }) }}</div>
        </div>
      </div>

      <div v-if="placedOrder" class="success-message" role="status">
        {{
          t('restocking.orderSuccess', {
            orderNumber: placedOrder.order_number,
            date: formatDate(placedOrder.expected_delivery)
          })
        }}
        <router-link to="/orders" class="success-link">{{ t('restocking.viewOrders') }}</router-link>
      </div>
      <div v-if="orderError" class="error" role="alert">{{ orderError }}</div>

      <div v-if="recommendation" class="card">
        <div class="card-header">
          <h3 class="card-title">
            {{ t('restocking.recommendations') }} ({{ items.length }})
            <span v-if="pending" class="updating">{{ t('restocking.updating') }}</span>
          </h3>
          <button class="place-order-btn" :disabled="!canPlaceOrder" @click="placeOrder">
            {{ submitting ? t('restocking.placingOrder') : t('restocking.placeOrder') }}
          </button>
        </div>

        <div v-if="items.length === 0" class="empty-state">{{ t('restocking.empty') }}</div>
        <div v-else class="table-container" :class="{ stale: pending }">
          <table class="restock-table">
            <thead>
              <tr>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.itemName') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th class="num">{{ t('restocking.table.forecastedDemand') }}</th>
                <th class="num">{{ t('restocking.table.recommendedQty') }}</th>
                <th class="num">{{ t('restocking.table.unitCost') }}</th>
                <th class="num">{{ t('restocking.table.lineTotal') }}</th>
                <th class="num">{{ t('restocking.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in items" :key="item.item_sku">
                <td><strong>{{ item.item_sku }}</strong></td>
                <td>{{ translateProductName(item.item_name) }}</td>
                <td>
                  <span :class="['badge', item.trend]">{{ t(`trends.${item.trend}`) }}</span>
                </td>
                <td class="num">{{ item.forecasted_demand.toLocaleString() }}</td>
                <td class="num">
                  <template v-if="item.is_partial">
                    {{
                      t('restocking.partialQty', {
                        qty: item.recommended_quantity.toLocaleString(),
                        forecast: item.forecasted_demand.toLocaleString()
                      })
                    }}
                    <span class="badge warning partial-badge">{{ t('restocking.partial') }}</span>
                  </template>
                  <template v-else>{{ item.recommended_quantity.toLocaleString() }}</template>
                </td>
                <td class="num">{{ fmtUnit(item.unit_cost) }}</td>
                <td class="num"><strong>{{ fmt(item.line_total) }}</strong></td>
                <td class="num">{{ t('restocking.days', { count: item.lead_time_days }) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.budget-card {
  padding: 1.5rem;
}

.budget-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 1rem;
  flex-wrap: wrap;
}

.budget-value {
  font-size: 2.5rem;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.025em;
  line-height: 1.1;
}

.budget-number {
  width: 140px;
  padding: 0.5rem 0.75rem;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  font-size: 0.938rem;
  color: #334155;
}

.budget-slider {
  width: 100%;
  margin-top: 1.25rem;
  accent-color: #2563eb;
  cursor: pointer;
}

.slider-scale {
  display: flex;
  justify-content: space-between;
  font-size: 0.75rem;
  color: #64748b;
  margin-top: 0.25rem;
}

.hint {
  margin-top: 0.75rem;
  font-size: 0.813rem;
  color: #64748b;
}

/* Dim content while a refresh is pending so users know it is not final. */
.stale {
  opacity: 0.55;
  transition: opacity 0.15s ease;
}

.stats-grid {
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
}

.stats-grid .stat-value {
  font-size: 1.75rem;
}

.updating {
  margin-left: 0.5rem;
  font-size: 0.813rem;
  font-weight: 500;
  color: #64748b;
}

.place-order-btn {
  background: #2563eb;
  color: white;
  border: none;
  border-radius: 8px;
  padding: 0.625rem 1.25rem;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.place-order-btn:hover:not(:disabled) {
  background: #1d4ed8;
}

.place-order-btn:disabled {
  background: #cbd5e1;
  color: #64748b;
  cursor: not-allowed;
}

.success-message {
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
  color: #065f46;
  padding: 1rem;
  border-radius: 8px;
  margin-bottom: 1.25rem;
  font-size: 0.938rem;
}

.success-link {
  margin-left: 0.5rem;
  color: #1d4ed8;
  font-weight: 600;
}

.empty-state {
  text-align: center;
  padding: 2.5rem 1rem;
  color: #64748b;
  font-size: 0.938rem;
}

.restock-table {
  min-width: 860px;
}

.restock-table th.num,
.restock-table td.num {
  text-align: right;
  white-space: nowrap;
}

.partial-badge {
  margin-left: 0.375rem;
}

@media (max-width: 640px) {
  .budget-value {
    font-size: 2rem;
  }
}
</style>
