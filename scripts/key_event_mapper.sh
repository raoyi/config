#!/bin/bash

# ==============================================================================
#  key_event_mapper.sh
#
#  Description:
#  Triggers 'input keyevent' and 'input text' commands by pressing a single key.
#  Supports WASD, arrow keys, and custom macro keys.
#  Responds instantly without needing to press Enter.
#
# ==============================================================================

# --- Function Definitions ---

# Displays help information
show_help() {
    clear
    echo "   Key Event Mapper Activated   "
    echo "------------------------------------------------"
    echo "  Use WASD or Arrow Keys for navigation"
    echo "    [w] / [?] - Up (DPAD_UP)"
    echo "    [s] / [?] - Down (DPAD_DOWN)"
    echo "    [a] / [?] - Left (DPAD_LEFT)"
    echo "    [d] / [?] - Right (DPAD_RIGHT)"
    echo ""
    echo "    [z] / [Enter] - Center/Start (DPAD_CENTER, 23)"
    echo "    [e]           - Confirm (ENTER, 66)"
    echo "    [b]           - Back (BACK)"
    echo ""
    echo "  --- Macros ---"
    echo "    [p] - Input WiFi password '11111111'"
    echo "    [m] - Input Gmail login info"
    echo "------------------------------------------------"
    echo "  Press keys directly, no Enter needed."
    echo "  Press [q] to quit the program."
    echo "------------------------------------------------"
}

# Cleanup function to restore terminal settings on exit
cleanup() {
    echo -e "\\nRestoring terminal settings and exiting..."
    stty "$original_stty_settings"
    exit 0
}

# --- Main Program ---

# Save the current terminal settings
original_stty_settings=$(stty -g)

# Trap the EXIT and INT signals to ensure the cleanup function is always called
trap cleanup EXIT INT

# Set the terminal to raw mode (read-immediately) and disable echo
stty raw -echo

# Display the help message
show_help

# Infinite loop to wait for key presses
while true; do
    # Read a single character. The -d '' makes it read until a NUL character,
    # which effectively reads one keypress, including Enter.
    read -rsn1 -d '' key

    # If the key is an ESC character, it might be an arrow key
    if [[ "$key" == $'\x1b' ]]; then
        # Immediately read the next two characters of the escape sequence
        read -rsn2 -t 0.1 rest_of_key
        # Combine them to form the full 3-byte sequence
        key+="$rest_of_key"
    fi

    # Use a case statement to match the pressed key
    case "$key" in
        # --- Arrow key escape sequences ---
        $'\x1b[A') # Up arrow
            echo "Sent: Up (DPAD_UP, 19)"
            input keyevent 19
            ;;
        $'\x1b[B') # Down arrow
            echo "Sent: Down (DPAD_DOWN, 20)"
            input keyevent 20
            ;;
        $'\x1b[D') # Left arrow
            echo "Sent: Left (DPAD_LEFT, 21)"
            input keyevent 21
            ;;
        $'\x1b[C') # Right arrow
            echo "Sent: Right (DPAD_RIGHT, 22)"
            input keyevent 22
            ;;

        # WASD and other function keys
        w|W)
            echo "Sent: Up (DPAD_UP, 19)"
            input keyevent 19
            ;;
        s|S)
            echo "Sent: Down (DPAD_DOWN, 20)"
            input keyevent 20
            ;;
        a|A)
            echo "Sent: Left (DPAD_LEFT, 21)"
            input keyevent 21
            ;;
        d|D)
            echo "Sent: Right (DPAD_RIGHT, 22)"
            input keyevent 22
            ;;

        # --- Corrected Enter key mapping ---
        # The empty case '' handles the Enter key with `read -d ''`
        ''|z|Z)
            echo "Sent: Center (DPAD_CENTER, 23)"
            input keyevent 23
            ;;

        e|E)
            echo "Sent: Confirm (ENTER, 66)"
            input keyevent 66
            ;;
        b|B)
            echo "Sent: Back (BACK, 4)"
            input keyevent 4
            ;;

        # --- Macros ---
        p|P)
            echo "Sent: WiFi password"
            input text input_your_WiFi_password
            sleep 3 # Add a small delay for stability
            input keyevent 66
            ;;
        m|M)
            echo "Sent: Mail info..."
            echo "1. Typing email..."
            input text input_gmail_addr_here
            sleep 5
            input keyevent 66 # Press Enter to go to password field
            
            echo "2. Waiting for password field..."
            sleep 5 # Wait for the next screen/field to load
            
            echo "3. Typing password..."
            input text input_password_here
            sleep 5
            input keyevent 66 # Press Enter to sign in
            ;;
        q|Q)
            # Break the loop if 'q' is pressed
            break
            ;;
        
        # Optional: If read times out, do nothing
        *)
            # For any other key, print a message
            echo "Unmapped key: '$key'"
            ;;
    esac
done

