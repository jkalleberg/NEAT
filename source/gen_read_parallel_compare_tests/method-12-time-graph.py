# importing the required module
import matplotlib.pyplot as plt

#Method 1 Points 
# x axis values - Values of N
x = [1000, 10000, 100000, 1000000, 10000000, 100000000]
# corresponding y axis values - Method1.py "real time"
y = [.092, .615, 6.038, 54.827, 588.842, 7125.087]
 
# plotting the points
plt.plot(x, y, label = "Method 1")


#Method 2 Points
# x axis values - Values of N
x = [1000, 10000, 100000, 1000000, 10000000, 100000000]
# corresponding y axis values - Method1.py "real time"
y = [.133, .101, .259, 2.312, 72.358, 705.246]
 
# plotting the points
plt.plot(x, y, label = "Method 2")



 
# naming the x axis
plt.xlabel('Value of N')
# naming the y axis
plt.ylabel('Method Elapsed time (sec)')
 
# giving a title to my graph
plt.title('Method1 and Method 2 - Elapsed time vs N')
 
# function to show the legend
plt.legend()
# function to show the plot
plt.show()
