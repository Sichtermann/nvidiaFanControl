#!/bin/bash

# Path to the PWM control
PWM_PATH="/sys/class/hwmon/hwmon7/pwm1"
ERROR_PWM=40

# Function to get GPU temperature
get_gpu_temp() {
    temp=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null)
    if [ $? -ne 0 ]; then
        echo "Error: Failed to get GPU temperature."
        return 1
    fi
    echo $temp
}

# Function to calculate and set fan speed
set_fan_speed() {
    local temp=$1
    local min_temp=40  # Set the minimum temperature for fan to start
    local max_temp=75  # Set the maximum expected temperature
    local min_pwm=18   # Min PWM value (fan off)
    local max_pwm=160  # Max PWM value (fan at full speed) could be 255

    # Calculate PWM value based on a linear curve
    if [ "$temp" -le "$min_temp" ]; then
        pwm_value=$min_pwm
    elif [ "$temp" -ge "$max_temp" ]; then
        pwm_value=$max_pwm
    else
        # Linear interpolation formula
        pwm_value=$(awk "BEGIN {print int(($min_pwm+($max_pwm-$min_pwm)*($temp-$min_temp)/($max_temp-$min_temp)))}")
    fi

    # Round PWM value to nearest multiple of 5
    pwm_value=$(( (pwm_value + 2) / 5 * 5 ))

    # Set the fan speed
    echo "$pwm_value" > "$PWM_PATH"
}

# Main loop
while true; do
    temp=$(get_gpu_temp)
    if [ $? -ne 0 ]; then
        echo "Setting fan speed to safe value due to error"
        echo "$ERROR_PWM" > "$PWM_PATH"
    else
        # Set the fan speed based on the temperature
        set_fan_speed $temp
    fi

    # Wait for a few seconds before checking again
    sleep 5
done% 
