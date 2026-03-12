import { useState, useEffect } from 'react'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  LineElement,
  PointElement,
  Title,
  Tooltip,
  Legend,
} from 'chart.js'
import { Bar, Line } from 'react-chartjs-2'

ChartJS.register(
  CategoryScale,
  LinearScale,
  BarElement,
  LineElement,
  PointElement,
  Title,
  Tooltip,
  Legend,
)

const STORAGE_KEY = 'api_key'

interface ScoreBucket {
  bucket: string
  count: number
}

interface ScoresResponse {
  lab_id: number
  lab_name: string
  buckets: ScoreBucket[]
}

interface TimelineEntry {
  date: string
  submissions: number
}

interface TimelineResponse {
  lab_id: number
  timeline: TimelineEntry[]
}

interface TaskPassRate {
  task_id: number
  task_name: string
  pass_rate: number
}

interface PassRatesResponse {
  lab_id: number
  tasks: TaskPassRate[]
}

interface Lab {
  id: number
  name: string
}

type FetchState<T> =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; data: T }
  | { status: 'error'; message: string }

function Dashboard() {
  const [labs, setLabs] = useState<Lab[]>([])
  const [selectedLabId, setSelectedLabId] = useState<number | null>(null)

  const [scoresState, setScoresState] = useState<FetchState<ScoresResponse>>({
    status: 'idle',
  })
  const [timelineState, setTimelineState] = useState<FetchState<TimelineResponse>>({
    status: 'idle',
  })
  const [passRatesState, setPassRatesState] = useState<FetchState<PassRatesResponse>>({
    status: 'idle',
  })

  // Fetch available labs on mount
  useEffect(() => {
    const token = localStorage.getItem(STORAGE_KEY)
    if (!token) return

    fetch('/labs/', {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json()
      })
      .then((data: Lab[]) => {
        setLabs(data)
        if (data.length > 0) {
          setSelectedLabId(data[0].id)
        }
      })
      .catch((err: Error) => {
        console.error('Failed to fetch labs:', err)
      })
  }, [])

  // Fetch analytics data when selected lab changes
  useEffect(() => {
    if (!selectedLabId) return

    const token = localStorage.getItem(STORAGE_KEY)
    if (!token) return

    const fetchScores = async () => {
      setScoresState({ status: 'loading' })
      try {
        const res = await fetch(`/analytics/scores?lab=${selectedLabId}`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data: ScoresResponse = await res.json()
        setScoresState({ status: 'success', data })
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : 'Unknown error'
        setScoresState({ status: 'error', message })
      }
    }

    const fetchTimeline = async () => {
      setTimelineState({ status: 'loading' })
      try {
        const res = await fetch(`/analytics/timeline?lab=${selectedLabId}`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data: TimelineResponse = await res.json()
        setTimelineState({ status: 'success', data })
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : 'Unknown error'
        setTimelineState({ status: 'error', message })
      }
    }

    const fetchPassRates = async () => {
      setPassRatesState({ status: 'loading' })
      try {
        const res = await fetch(`/analytics/pass-rates?lab=${selectedLabId}`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data: PassRatesResponse = await res.json()
        setPassRatesState({ status: 'success', data })
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : 'Unknown error'
        setPassRatesState({ status: 'error', message })
      }
    }

    fetchScores()
    fetchTimeline()
    fetchPassRates()
  }, [selectedLabId])

  const barChartData = scoresState.status === 'success'
    ? {
        labels: scoresState.data.buckets.map((b) => b.bucket),
        datasets: [
          {
            label: 'Submissions',
            data: scoresState.data.buckets.map((b) => b.count),
            backgroundColor: 'rgba(54, 162, 235, 0.6)',
            borderColor: 'rgba(54, 162, 235, 1)',
            borderWidth: 1,
          },
        ],
      }
    : { labels: [], datasets: [] }

  const lineChartData = timelineState.status === 'success'
    ? {
        labels: timelineState.data.timeline.map((t) => t.date),
        datasets: [
          {
            label: 'Submissions per Day',
            data: timelineState.data.timeline.map((t) => t.submissions),
            borderColor: 'rgba(75, 192, 192, 1)',
            backgroundColor: 'rgba(75, 192, 192, 0.2)',
            tension: 0.1,
            fill: true,
          },
        ],
      }
    : { labels: [], datasets: [] }

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
  }

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>Dashboard</h1>
        <div className="lab-selector">
          <label htmlFor="lab-select">Select Lab: </label>
          <select
            id="lab-select"
            value={selectedLabId ?? ''}
            onChange={(e) => setSelectedLabId(Number(e.target.value) || null)}
          >
            <option value="" disabled>
              Select a lab
            </option>
            {labs.map((lab) => (
              <option key={lab.id} value={lab.id}>
                {lab.name}
              </option>
            ))}
          </select>
        </div>
      </header>

      {!selectedLabId && labs.length > 0 && (
        <p>Please select a lab to view analytics.</p>
      )}

      {labs.length === 0 && (
        <p>No labs available. Please check your connection.</p>
      )}

      {selectedLabId && (
        <>
          <section className="chart-section">
            <h2>Score Distribution</h2>
            <div className="chart-container">
              {scoresState.status === 'loading' && <p>Loading...</p>}
              {scoresState.status === 'error' && (
                <p>Error: {scoresState.message}</p>
              )}
              {scoresState.status === 'success' && (
                <Bar data={barChartData} options={chartOptions} />
              )}
            </div>
          </section>

          <section className="chart-section">
            <h2>Submissions Timeline</h2>
            <div className="chart-container">
              {timelineState.status === 'loading' && <p>Loading...</p>}
              {timelineState.status === 'error' && (
                <p>Error: {timelineState.message}</p>
              )}
              {timelineState.status === 'success' && (
                <Line data={lineChartData} options={chartOptions} />
              )}
            </div>
          </section>

          <section className="table-section">
            <h2>Pass Rates per Task</h2>
            <div className="table-container">
              {passRatesState.status === 'loading' && <p>Loading...</p>}
              {passRatesState.status === 'error' && (
                <p>Error: {passRatesState.message}</p>
              )}
              {passRatesState.status === 'success' && (
                <table>
                  <thead>
                    <tr>
                      <th>Task ID</th>
                      <th>Task Name</th>
                      <th>Pass Rate (%)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {passRatesState.data.tasks.map((task) => (
                      <tr key={task.task_id}>
                        <td>{task.task_id}</td>
                        <td>{task.task_name}</td>
                        <td>{task.pass_rate.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  )
}

export default Dashboard
