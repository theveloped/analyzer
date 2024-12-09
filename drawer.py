# def draw_rectangle_a(L, D, R=None):
#     # Set the typical terminal width
#     terminal_width = 80
    
#     # Parameter labels to the left of the rectangle
#     parameter_label = f'D={D}, L={L}, R={R if R is not None else "None"}'
    
#     # Calculate padding width for centering the rectangle and labels
#     max_rectangle_width = max(len(parameter_label) + D + 3, D + 2)
#     padding_width = (terminal_width - max_rectangle_width) // 2
#     padding = ' ' * padding_width
#     parameter_label_padding = ' ' * (max_rectangle_width - len(parameter_label) - D - 3)
    
#     # Prepare the top and bottom parts of the rectangle
#     if R is None or R <= 0:
#         # No rounded corners
#         top_bottom = '+' + '-' * D + '+'
#     elif 0 < R < D / 2:
#         # Rounded corners
#         rounded_part = ' ' * int(R) + '-' * int(D - 2 * R) + ' ' * int(R)
#         top_bottom = '+' + rounded_part + '+'
#     else:
#         # If R is not within bounds, treat as no rounded corners
#         top_bottom = '+' + '-' * D + '+'

#     # Print the top part of the rectangle
#     print(padding + parameter_label + parameter_label_padding + top_bottom)
    
#     # Print the sides of the rectangle
#     for _ in range(L):
#         if R > 0 and R < D / 2:
#             # If the corners are rounded, adjust the side lines accordingly
#             side_padding = ' ' * int(R)
#             print(padding + ' ' * (len(parameter_label) + 3) + '|' + side_padding + ' ' * int(D - 2 * R) + side_padding + '|')
#         else:
#             # Straight sides
#             print(padding + ' ' * (len(parameter_label) + 3) + '|' + ' ' * D + '|')

#     # Print the bottom part of the rectangle
#     print(padding + ' ' * (len(parameter_label) + 3) + top_bottom)

# # Example usage
# draw_rectangle_a(5, 20, 3)
# print()  # Space between rectangles
# draw_rectangle_a(3, 15, 2)
# print()  # Space between rectangles
# # draw_rectangle_a(4, 25, None)


# def draw_rectangle(L, D, R=None):
#     # Set the typical terminal width
#     terminal_width = 80
    
#     # Parameter labels to the left of the rectangle
#     parameter_label = f'D={D}, L={L}, R={R if R is not None else "None"}'
    
#     # Calculate padding width for centering the rectangle and labels
#     max_rectangle_width = max(len(parameter_label) + D + 3, D + 2)
#     padding_width = (terminal_width - max_rectangle_width) // 2
#     padding = ' ' * padding_width
#     parameter_label_padding = ' ' * (max_rectangle_width - len(parameter_label) - D - 3)
    
#     # Prepare the top and bottom parts of the rectangle
#     if R is None or R <= 0:
#         # No rounded corners
#         top_bottom = '+' + '-' * D + '+'
#     elif 0 < R < D / 2:
#         # Rounded corners
#         rounded_part = ' ' * int(R) + '-' * int(D - 2 * R) + ' ' * int(R)
#         top_bottom = '+' + rounded_part + '+'
#     else:
#         # If R is not within bounds, treat as no rounded corners
#         top_bottom = '+' + '-' * D + '+'

#     # Print the top part of the rectangle
#     print(padding + parameter_label + parameter_label_padding + top_bottom)
    
#     # Print the sides of the rectangle
#     for _ in range(L):
#         if R > 0 and R < D / 2:
#             # If the corners are rounded, adjust the side lines accordingly
#             side_padding = ' ' * int(R)
#             print(padding + ' ' * (len(parameter_label) + 3) + '|' + side_padding + ' ' * int(D - 2 * R) + side_padding + '|')
#         else:
#             # Straight sides
#             print(padding + ' ' * (len(parameter_label) + 3) + '|' + ' ' * D + '|')

#     # Print the bottom part of the rectangle
#     print(padding + ' ' * (len(parameter_label) + 3) + top_bottom)

# # Example usage
# draw_rectangle(5, 20, 3)
# print()  # Space between rectangles
# draw_rectangle(3, 15, 2)
# print()  # Space between rectangles
# draw_rectangle(4, 25, None)





def draw_rectangle_c(L, D, R=None):
    # Set the typical terminal width
    terminal_width = 80

    # Parameter labels to the left of the rectangle
    parameter_label = f'D={D}, L={L}, R={R if R is not None else "None"}'

    # Calculate padding width for centering the rectangle and labels
    max_rectangle_width = max(len(parameter_label) + D + 3, D + 2)
    padding_width = (terminal_width - max_rectangle_width) // 2
    padding = ' ' * padding_width
    parameter_label_padding = ' ' * (max_rectangle_width - len(parameter_label) - D - 3)

    # Prepare the top and bottom parts of the rectangle
    if R is None or R <= 0:
        # No rounded corners
        top_bottom = '+' + '-' * D + '+'
    elif 0 < R < D / 2:
        # Rounded corners
        rounded_part = '/' + '-' * int(D - 2 * R) + '\\'
        top_bottom = '+' + rounded_part + '+'
    else:
        # If R is not within bounds, treat as no rounded corners
        top_bottom = '+' + '-' * D + '+'

    # Print the top part of the rectangle
    print(padding + parameter_label + parameter_label_padding + top_bottom)

    # Print the sides of the rectangle
    for i in range(L):
        if R and R > 0 and R < D / 2:
            if i == 0 or i == L - 1:
                continue  # Skip the rounded corners lines
            # Adjust side lines with rounded corners
            print(padding + ' ' * (len(parameter_label) + 3) + '|' + ' ' * D + '|')
        else:
            # Straight sides
            print(padding + ' ' * (len(parameter_label) + 3) + '|' + ' ' * D + '|')

    # Print the bottom part of the rectangle
    print(padding + ' ' * (len(parameter_label) + 3) + top_bottom)

# Example usage
draw_rectangle_c(5, 20, 3)
draw_rectangle_c(3, 15, 2)
draw_rectangle_c(4, 25, None)