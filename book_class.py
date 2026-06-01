name: WC Signup — Manual

# Manual trigger only. Use this from the GitHub mobile app to book a class
# immediately (or test a booking with dry_run).
# Scheduled bookings each get their own workflow file (book-YYYY-MM-DD-HHMM.yml)
# created by the dashboard — no background polling needed.

on:
  workflow_dispatch:
    inputs:
      class_date:
        description: 'Class date (YYYY-MM-DD)'
        required: true
        type: string
      class_time:
        description: 'Class time (HH:MM, 24h Israel time)'
        required: true
        type: string
      category_filter:
        description: 'Category filter (optional, e.g. "W.O.D Hall A")'
        required: false
        type: string
        default: ''
      dry_run:
        description: 'Dry run — log only, no actual booking'
        required: false
        type: boolean
        default: false

jobs:
  book:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Book class
        env:
          TZ:                       Asia/Jerusalem
          ARBOX_EMAIL:              ${{ secrets.ARBOX_EMAIL }}
          ARBOX_PASSWORD:           ${{ secrets.ARBOX_PASSWORD }}
          ARBOX_WHITELABEL:         ${{ secrets.ARBOX_WHITELABEL }}
          ARBOX_BOXES_ID:           ${{ secrets.ARBOX_BOXES_ID }}
          ARBOX_LOCATIONS_BOX_ID:   ${{ secrets.ARBOX_LOCATIONS_BOX_ID }}
          ARBOX_MEMBERSHIP_USER_ID: ${{ secrets.ARBOX_MEMBERSHIP_USER_ID }}
        run: |
          EXTRA_FLAGS=""
          if [ "${{ inputs.dry_run }}" = "true" ]; then
            EXTRA_FLAGS="--dry-run"
          fi
          python book_class.py \
            --class-date "${{ inputs.class_date }}" \
            --class-time "${{ inputs.class_time }}" \
            --category-filter "${{ inputs.category_filter }}" \
            --wait-for-window \
            $EXTRA_FLAGS
